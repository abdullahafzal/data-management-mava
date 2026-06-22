#!/usr/bin/env python
# coding: utf-8
"""
Instant Checkmate — Step 2 of your process (ICM only; Spy Dialer comes later).

Your process:
  1. Export from NY state site → ``file/newOne.xlsx``
  2. **Individuals** → ICM name search → scrape **phones**, **emails**, **locations** from each report
  3. Keep only people whose **Locations** match the facility city/state (near same address)
  - Corp / Inc / LLC in **owner name** → skip here; phone-column automation later

Output columns: ``phone_numbers``, ``emails``, ``locations``, ``report_name``, ``page_url``, plus facility/owner source fields.

Run:
  python3 icm_name_search_automation.py
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any, List, Optional

import pandas as pd
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from automation.icm import second_site_automation as icm
from automation.services.browser import looks_like_browser_closed

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# =============================================================================
# CONFIG
# =============================================================================
INPUT_XLSX = str(_PROJECT_ROOT / "file" / "newOne.xlsx")
OUTPUT_XLSX = str(_PROJECT_ROOT / "icm_name_search_output.xlsx")

# Excel columns (case-insensitive header match)
COL_OWNER = "Owner Name "
COL_FACILITY_CITY = "Facility City"
COL_FACILITY_STATE = "Facility State"
COL_FACILITY_STREET = "Facility Street"
COL_FACILITY_NUM = "Facility #"
COL_FACILITY_NAME = "Facility Name"
COL_OWNER_OVERFLOW = "Owner Name Overflow"
COL_FACILITY_NAME_OVERFLOW = "Facility Name Overflow"

# Corp / Inc / LLC → phone automation later, not name search
_CORPORATE_RE = re.compile(
    r"\b(corp|corporation|inc|incorporated|llc|l\.l\.c\.|ltd|limited|co\.|company)\b",
    re.IGNORECASE,
)

# What to type in ICM "City" field: "city" (e.g. BRONX), "street", or "both"
CITY_FIELD_SOURCE = "city"
# Only open View on cards whose preview Locations match facility (skip unrelated states)
FILTER_CARDS_BY_PREVIEW_LOCATION = True
# After opening each report, verify Locations (report scrape, or card preview fallback)
VERIFY_LOCATION_ON_REPORT = True
REQUIRE_STATE_IN_LOCATIONS = True
REQUIRE_CITY_OR_STREET_IN_LOCATIONS = True
RETURN_TO_RESULTS_WAIT_SEC = 15  # after driver.back() from a report

# Max ICM name searches (individual owners only). None = all individuals in file.
MAX_ROWS: Optional[int] = 2
# Optional: only read first N Excel rows (faster dev runs). None = entire workbook.
MAX_INPUT_ROWS: Optional[int] = None
PAUSE_BETWEEN_SEARCHES = 2.0
HEADLESS = False  # visible Chrome (needed for logged-in ICM profile)
DEDUPLICATE_SEARCHES = True  # same first+last+city+state searched once; rows still get output via merge
NAME_SEARCH_RESULTS_WAIT_SEC = 60  # wait for result cards / View links after Search
# False = output only ICM name-search rows (not 11k+ corporate/unparseable skipped rows)
INCLUDE_SKIPPED_IN_OUTPUT = False
# Skip opening a second View when ICM shows the same create-record link twice
SKIP_DUPLICATE_ICM_CARDS = True

US_STATE_TO_NAME = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}


@dataclass
class NameSearchResultRow:
    """One scraped ICM report row, plus source facility fields from the input Excel."""

    # --- source (from newOne.xlsx / prior file) ---
    input_row_index: int = 0
    facility_number: Optional[str] = None
    facility_name: Optional[str] = None
    facility_street: Optional[str] = None
    facility_city: Optional[str] = None
    facility_state: Optional[str] = None
    owner_name_raw: Optional[str] = None
    owner_type: Optional[str] = None  # individual | corporate | unparseable
    icm_search_mode: Optional[str] = None  # name | skipped_corporate | skipped_unparseable
    search_first_name: Optional[str] = None
    search_last_name: Optional[str] = None
    search_middle: Optional[str] = None
    search_city_typed: Optional[str] = None
    search_state_typed: Optional[str] = None

    # --- search result card ---
    result_card_index: int = 0
    result_card_name: Optional[str] = None
    result_card_locations: Optional[str] = None
    result_match_label: Optional[str] = None

    # --- report scrape (aligned with second_site_automation.ResultRow) ---
    record_index: int = 1
    owner_from_results: Optional[str] = None
    report_name: Optional[str] = None
    phone_numbers: Optional[str] = None
    emails: Optional[str] = None
    locations: Optional[str] = None
    page_url: Optional[str] = None
    location_match: Optional[bool] = None
    status: str = "ok"
    error: Optional[str] = None

    extra_source: dict[str, Any] = field(default_factory=dict)


def _norm_col_map(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().lower(): c for c in df.columns}


def _cell_str(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def is_corporate_owner(owner: str, owner_overflow: str = "") -> bool:
    """Corp/Inc/LLC in the *owner* name fields only (not facility name overflow)."""
    blob = " ".join(_cell_str(x) for x in (owner, owner_overflow) if _cell_str(x))
    return bool(_CORPORATE_RE.search(blob))


def classify_owner(owner: str, owner_overflow: str = "") -> str:
    """Return individual | corporate | unparseable."""
    if is_corporate_owner(owner, owner_overflow):
        return "corporate"
    first, last, _mid = parse_owner_name(owner)
    if not first or not last:
        return "unparseable"
    return "individual"


def parse_owner_name(raw: str) -> tuple[str, str, str]:
    """
    Split owner into first, last, middle initial.
    "ANTONIO K ASSALONE" -> ("ANTONIO", "ASSALONE", "K")
    "VAN BISHKOFF" -> ("VAN", "BISHKOFF", "")
    """
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return "", "", ""
    parts = s.split()
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    if len(parts) == 3 and len(parts[1]) <= 2:
        return parts[0], parts[2], parts[1]
    return parts[0], parts[-1], " ".join(parts[1:-1])


def state_for_dropdown(state_raw: str) -> str:
    s = _cell_str(state_raw).upper()
    if not s:
        return ""
    if len(s) == 2 and s in US_STATE_TO_NAME:
        return US_STATE_TO_NAME[s]
    return _cell_str(state_raw)


def city_for_search_form(city: str, street: str) -> str:
    mode = (CITY_FIELD_SOURCE or "street").strip().lower()
    c, st = _cell_str(city), _cell_str(street)
    if mode == "city":
        return c or st
    if mode == "both" and c and st:
        return f"{st}, {c}" if st else c
    return st or c


def _normalize_location_blob(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    t = re.sub(r"[^\w\s,]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _location_tokens(city: str, state: str, street: str, city_typed: str) -> tuple[list[str], list[str]]:
    """Tokens that must appear in card Locations (city/street) and state variants."""
    st_abbr = _cell_str(state).upper()[:2]
    st_full = state_for_dropdown(state)
    state_needles = {x.lower() for x in (st_abbr, st_full, _cell_str(state)) if x}

    city_needles: list[str] = []
    for val in (city_typed, city, street):
        v = _cell_str(val)
        if not v:
            continue
        city_needles.append(v.lower())
        for chunk in re.split(r"[,/\s]+", v):
            chunk = chunk.strip().lower()
            if len(chunk) >= 3:
                city_needles.append(chunk)
    # dedupe preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for n in city_needles:
        if n and n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered, list(state_needles)


def location_lines_match_search(
    location_lines: List[str],
    facility_city: str,
    facility_state: str,
    facility_street: str,
    city_typed: str = "",
) -> bool:
    if not location_lines:
        return False
    blob = _normalize_location_blob(" | ".join(location_lines))
    city_tokens, state_tokens = _location_tokens(
        facility_city, facility_state, facility_street, city_typed
    )

    if REQUIRE_STATE_IN_LOCATIONS and state_tokens:
        if not any(st in blob for st in state_tokens):
            return False

    if not REQUIRE_CITY_OR_STREET_IN_LOCATIONS:
        return True

    if not city_tokens:
        return True

    return any(tok in blob for tok in city_tokens)


def location_lines_match_facility(
    location_lines: List[str],
    facility_city: str,
    facility_state: str,
    facility_street: str,
) -> bool:
    """Match report/card locations against facility city, state, and street (not search-form city)."""
    return location_lines_match_search(
        location_lines,
        facility_city,
        facility_state,
        facility_street,
        city_typed="",
    )


def _split_location_field(loc: Optional[str]) -> List[str]:
    if not loc:
        return []
    return [x.strip() for x in str(loc).split("|") if x.strip()]


def load_input_rows(path: str, limit_rows: Optional[int] = None) -> list[dict[str, Any]]:
    df = pd.read_excel(path)
    if limit_rows is not None:
        df = df.head(int(limit_rows))
    cmap = _norm_col_map(df)

    def col(*candidates: str) -> Optional[str]:
        for c in candidates:
            k = c.strip().lower()
            if k in cmap:
                return cmap[k]
        return None

    c_owner = col(COL_OWNER, "owner name", "owner")
    c_city = col(COL_FACILITY_CITY, "facility city", "city")
    c_state = col(COL_FACILITY_STATE, "facility state", "state")
    c_street = col(COL_FACILITY_STREET, "facility street", "street")
    c_num = col(COL_FACILITY_NUM, "facility #", "facility number")
    c_fname = col(COL_FACILITY_NAME, "facility name")
    c_own_ov = col(COL_OWNER_OVERFLOW, "owner name overflow")
    c_fac_ov = col(COL_FACILITY_NAME_OVERFLOW, "facility name overflow")

    if not c_owner:
        raise ValueError(f"Owner column not found in {path}. Columns: {list(df.columns)}")

    rows: list[dict[str, Any]] = []
    for i, row in df.iterrows():
        owner = _cell_str(row[c_owner])
        if not owner:
            continue
        own_ov = _cell_str(row[c_own_ov]) if c_own_ov else ""
        fac_ov = _cell_str(row[c_fac_ov]) if c_fac_ov else ""
        otype = classify_owner(owner, own_ov)

        fc = _cell_str(row[c_city]) if c_city else ""
        fs = _cell_str(row[c_state]) if c_state else ""
        fst = _cell_str(row[c_street]) if c_street else ""
        extra = {
            str(c): row[c]
            for c in df.columns
            if c not in (c_owner, c_city, c_state, c_street, c_num, c_fname)
        }

        first, last, mid = "", "", ""
        if otype == "individual":
            first, last, mid = parse_owner_name(owner)

        rows.append(
            {
                "input_row_index": int(i) + 2,
                "facility_number": _cell_str(row[c_num]) if c_num else None,
                "facility_name": _cell_str(row[c_fname]) if c_fname else None,
                "facility_street": fst or None,
                "facility_city": fc or None,
                "facility_state": fs or None,
                "owner_name_raw": owner,
                "owner_type": otype,
                "search_first_name": first or None,
                "search_last_name": last or None,
                "search_middle": mid or None,
                "search_city_typed": city_for_search_form(fc, fst) if otype == "individual" else None,
                "search_state_typed": state_for_dropdown(fs) if otype == "individual" else None,
                "extra_source": extra,
            }
        )
    return rows


def rows_for_name_search(all_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in all_rows if r.get("owner_type") == "individual"]


def skipped_rows_as_results(all_rows: list[dict[str, Any]]) -> List[NameSearchResultRow]:
    """Corporate / unparseable owners — recorded in output, no browser search."""
    out: List[NameSearchResultRow] = []
    for row in all_rows:
        otype = row.get("owner_type")
        if otype == "individual":
            continue
        mode = "skipped_corporate" if otype == "corporate" else "skipped_unparseable"
        msg = (
            "Corp/Inc/LLC — use phone search (second_site_automation.py) when phone column is ready"
            if otype == "corporate"
            else "Could not split owner into first and last name"
        )
        out.append(
            NameSearchResultRow(
                input_row_index=row.get("input_row_index", 0),
                facility_number=row.get("facility_number"),
                facility_name=row.get("facility_name"),
                facility_street=row.get("facility_street"),
                facility_city=row.get("facility_city"),
                facility_state=row.get("facility_state"),
                owner_name_raw=row.get("owner_name_raw"),
                owner_type=otype,
                icm_search_mode=mode,
                status=mode,
                error=msg,
                extra_source=dict(row.get("extra_source") or {}),
            )
        )
    return out


def _search_dedupe_key(r: dict[str, Any]) -> str:
    return "|".join(
        [
            (r.get("search_first_name") or "").lower(),
            (r.get("search_last_name") or "").lower(),
            (r.get("search_city_typed") or "").lower(),
            (r.get("search_state_typed") or "").lower(),
        ]
    )


def _save_excel(df: pd.DataFrame, path: str) -> str:
    try:
        df.to_excel(path, index=False)
        return path
    except PermissionError:
        p = Path(path)
        alt = p.with_name(f"{p.stem}_{datetime.now():%Y%m%d_%H%M%S}{p.suffix}")
        df.to_excel(str(alt), index=False)
        print(f"[ICM-Name] Locked {path}; wrote {alt}")
        return str(alt)


def _wait_icm_report_page(driver, timeout: Optional[float] = None) -> None:
    t = float(timeout if timeout is not None else icm.WAIT_SEC)

    def ready(d):
        u = (d.current_url or "").lower()
        if "create-record" not in u and "/dashboard/reports/" not in u:
            return False
        txt = icm._main_text(d)
        return len(txt) > 60

    WebDriverWait(driver, t).until(ready)
    time.sleep(0.5)


def _icm_name_tab_active(driver) -> bool:
    try:
        for el in driver.find_elements(
            By.CSS_SELECTOR,
            "li[role='tab'][aria-label='Name search tab'][aria-selected='true']",
        ):
            if el.is_displayed():
                return True
    except Exception:
        pass
    try:
        for el in driver.find_elements(By.XPATH, "//label[contains(.,'First Name')]/following::input[1]"):
            if el.is_displayed():
                return True
    except Exception:
        pass
    return False


def _click_name_tab(driver) -> None:
    print("[ICM-Name] Activating Name tab...")
    icm.dismiss_overlays_if_any(driver)
    deadline = time.time() + min(float(icm.WAIT_SEC), 55)
    last: Optional[BaseException] = None
    while time.time() < deadline:
        if _icm_name_tab_active(driver):
            return
        try:
            li = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "li[role='tab'][aria-label='Name search tab']")
                )
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
                li,
            )
            time.sleep(0.5)
            if _icm_name_tab_active(driver):
                return
        except Exception as e:
            last = e
        try:
            ok = driver.execute_script(
                "var e=document.querySelector(\"li[role='tab'][aria-label='Name search tab']\");"
                "if(!e)return false; e.click(); return true;"
            )
            if ok:
                time.sleep(0.5)
                if _icm_name_tab_active(driver):
                    return
        except Exception as e:
            last = e
        time.sleep(0.35)
    raise TimeoutException(f"Name tab did not activate: {last}")


def _find_labeled_input(driver, label_fragment: str):
    frag = label_fragment.lower()
    xps = [
        f"//label[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{frag}')]/following::input[1]",
        f"//*[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{frag}')]/following::input[1]",
    ]
    for xp in xps:
        for el in driver.find_elements(By.XPATH, xp):
            try:
                if el.is_displayed() and (el.get_attribute("type") or "").lower() != "hidden":
                    return el
            except Exception:
                continue
    return None


def _set_input_value(driver, el, value: str) -> None:
    icm.js_click(driver, el)
    try:
        el.clear()
    except Exception:
        pass
    el.send_keys(value)
    if (el.get_attribute("value") or "").strip():
        return
    driver.execute_script(
        """
        const el = arguments[0];
        const val = arguments[1];
        el.focus();
        el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        el,
        value,
    )


def _select_state_dropdown(driver, state_label: str) -> None:
    if not state_label:
        return
    sel_el = None
    for el in driver.find_elements(By.TAG_NAME, "select"):
        try:
            if not el.is_displayed():
                continue
            parent_txt = ""
            try:
                parent_txt = el.find_element(By.XPATH, "./ancestor::*[position()<=4]").text.lower()
            except Exception:
                pass
            if "state" in parent_txt or "all states" in (el.text or "").lower():
                sel_el = el
                break
        except Exception:
            continue
    if sel_el is None:
        sel_el = _find_labeled_input(driver, "state")
    if sel_el is None:
        print("[ICM-Name] Warning: state dropdown not found; continuing.")
        return
    tag = (sel_el.tag_name or "").lower()
    if tag == "select":
        Select(sel_el).select_by_visible_text(state_label)
        return
    _set_input_value(driver, sel_el, state_label)


def step_enter_name_and_search(driver, row: dict[str, Any]) -> str:
    if icm._icm_is_login_page(driver):
        icm.ensure_icm_session(driver)
    _click_name_tab(driver)
    first = row["search_first_name"]
    last = row["search_last_name"]
    mid = row.get("search_middle") or ""
    city = row.get("search_city_typed") or ""
    state = row.get("search_state_typed") or ""

    print(f"[ICM-Name] Search: {first} {last} | city={city!r} | state={state!r}")

    fn = _find_labeled_input(driver, "first name")
    ln = _find_labeled_input(driver, "last name")
    if fn is None or ln is None:
        raise TimeoutException("First/Last name inputs not found on Name tab")

    _set_input_value(driver, fn, first)
    _set_input_value(driver, ln, last)

    mi = _find_labeled_input(driver, "m.i")
    if mi is None:
        mi = _find_labeled_input(driver, "middle")
    if mi and mid:
        _set_input_value(driver, mi, mid)

    city_inp = _find_labeled_input(driver, "city")
    if city_inp and city:
        _set_input_value(driver, city_inp, city)

    _select_state_dropdown(driver, state)

    age = row.get("search_age") or ""
    age_inp = _find_labeled_input(driver, "age")
    if age_inp and age:
        _set_input_value(driver, age_inp, str(age))

    icm._click_search_button(driver)
    time.sleep(1.0)
    _wait_for_name_search_results(driver)
    url = driver.current_url
    print("[ICM-Name] Results URL:", url)
    return url


def _find_result_view_links(driver) -> List:
    """View links on the name-search results list (main content only)."""
    links: List = []
    seen: set[str] = set()
    xps = (
        "//main//a[contains(@class,'reportLink') and contains(normalize-space(.),'View')]",
        "//main//a[contains(@href,'create-record')][contains(normalize-space(.),'View')]",
        "//main//a[contains(@class,'button-link')][contains(normalize-space(.),'View')]",
        "//a[contains(@class,'_reportLink_')][contains(normalize-space(.),'View')]",
        "//a[contains(@class,'reportLink')][contains(normalize-space(.),'View')]",
    )
    for xp in xps:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                href = (el.get_attribute("href") or "").strip()
                key = href or str(id(el))
                if key in seen:
                    continue
                try:
                    if not el.is_displayed():
                        continue
                except Exception:
                    continue
                lab = (icm.safe_text(el) or "").lower()
                if "view" not in lab:
                    continue
                if "review" in lab or "overview" in lab:
                    continue
                seen.add(key)
                links.append(el)
        except Exception:
            continue
    return links


def _card_from_view_link(link) -> Any:
    """Nearest ancestor that looks like a search-result card (has h2 + View)."""
    xps = (
        "./ancestor::div[contains(@class,'searchResult')][1]",
        "./ancestor::div[contains(@class,'SearchResult')][1]",
        "./ancestor::div[.//h2][.//a[contains(.,'View')]][1]",
    )
    for xp in xps:
        try:
            return link.find_element(By.XPATH, xp)
        except Exception:
            continue
    try:
        return link.find_element(By.XPATH, "./ancestor::div[.//h2][1]")
    except Exception:
        return link


def _find_search_result_cards(driver) -> List:
    cards: List = []
    seen_ids: set[int] = set()

    def _add(elements: List) -> None:
        for el in elements:
            eid = id(el)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            cards.append(el)

    for sel in (
        "div[class*='_searchResultCard_']",
        "div[class*='searchResultCard']",
        "div[class*='SearchResultCard']",
    ):
        try:
            found = driver.find_elements(By.CSS_SELECTOR, sel)
            if found:
                visible = [c for c in found if c.is_displayed()]
                _add(visible if visible else found)
        except Exception:
            continue

    if not cards:
        for link in _find_result_view_links(driver):
            _add([_card_from_view_link(link)])

    if not cards:
        try:
            for h2 in driver.find_elements(By.XPATH, "//main//h2"):
                try:
                    if not h2.is_displayed():
                        continue
                    card = h2.find_element(
                        By.XPATH,
                        "./ancestor::div[.//a[contains(.,'View')]][1]",
                    )
                    _add([card])
                except Exception:
                    continue
        except Exception:
            pass

    return cards


def _wait_for_name_search_results(driver, timeout: Optional[float] = None) -> int:
    """
    Wait until ICM renders the results list (cards or View links).
    Do not use generic 'Results' text — that appears before cards load.
    """
    sec = float(timeout if timeout is not None else NAME_SEARCH_RESULTS_WAIT_SEC)
    print(f"[ICM-Name] Waiting up to {int(sec)}s for result cards / View links...")
    end = time.time() + sec
    while time.time() < end:
        icm.dismiss_overlays_if_any(driver)
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 3);")
        except Exception:
            pass
        cards = _find_search_result_cards(driver)
        if cards:
            print(f"[ICM-Name] Found {len(cards)} result card(s).")
            time.sleep(0.5)
            return len(cards)
        time.sleep(0.6)

    cards = _find_search_result_cards(driver)
    if cards:
        print(f"[ICM-Name] Found {len(cards)} result card(s) (late).")
        return len(cards)
    print("[ICM-Name] No result cards or View links on search page after wait.")
    return 0


def _card_locations(card) -> List[str]:
    lines: List[str] = []
    try:
        for title in card.find_elements(By.XPATH, ".//*[contains(@class,'_title_') or contains(@class,'title')]"):
            if "location" not in (icm.safe_text(title) or "").lower():
                continue
            parent = title.find_element(By.XPATH, "./ancestor::div[1]")
            for p in parent.find_elements(
                By.XPATH,
                ".//p[contains(@class,'_text_')] | .//p[contains(@class,'d3dxc')]",
            ):
                t = icm.safe_text(p)
                if t:
                    lines.append(t)
    except Exception:
        pass
    if not lines:
        blob = icm.safe_text(card) or ""
        for ln in blob.splitlines():
            ln = ln.strip()
            if "," in ln and re.search(r"[A-Za-z]{2,}", ln):
                if "location" not in ln.lower() and "alias" not in ln.lower():
                    lines.append(ln)
    return lines


def _card_person_name(card) -> Optional[str]:
    try:
        for h in card.find_elements(By.TAG_NAME, "h2"):
            t = icm.safe_text(h)
            if t:
                return t
    except Exception:
        pass
    return None


def _card_match_label(card) -> Optional[str]:
    try:
        for p in card.find_elements(By.CSS_SELECTOR, "p[class*='_text_']"):
            t = icm.safe_text(p)
            if t and "match" in t.lower():
                return t
    except Exception:
        pass
    return None


def _card_view_href(card) -> Optional[str]:
    link = _card_view_link(card)
    if link is None:
        return None
    try:
        return (link.get_attribute("href") or "").strip() or None
    except Exception:
        return None


def _card_view_link(card):
    for sel in (
        "a[class*='_reportLink_']",
        "a.button-link.full",
        "a[href*='create-record']",
    ):
        for el in card.find_elements(By.CSS_SELECTOR, sel):
            try:
                if el.is_displayed() and "view" in (icm.safe_text(el) or "").lower():
                    return el
            except Exception:
                continue
    for el in card.find_elements(By.XPATH, ".//a[contains(.,'View')] | .//button[contains(.,'View')]"):
        try:
            if el.is_displayed():
                return el
        except Exception:
            continue
    return None


def _enumerate_result_cards(
    driver,
) -> list[tuple[int, Any, List[str], Optional[str], Optional[str]]]:
    """All persons on the results page."""
    cards = _find_search_result_cards(driver)
    out: list[tuple[int, Any, List[str], Optional[str], Optional[str]]] = []
    for idx, card in enumerate(cards, start=1):
        locs = _card_locations(card)
        name = _card_person_name(card)
        out.append((idx, card, locs, name, _card_match_label(card)))
    return out


# card_index, view_href, preview locations, name, match label (no WebElements — avoids stale refs)
MatchedCardMeta = tuple[int, Optional[str], List[str], Optional[str], Optional[str]]


def _filter_cards_for_facility(driver, row: dict[str, Any]) -> list[MatchedCardMeta]:
    """Cards whose preview Locations include facility city/state (and street when set)."""
    all_cards = _enumerate_result_cards(driver)
    total = len(all_cards)
    if not FILTER_CARDS_BY_PREVIEW_LOCATION:
        out: list[MatchedCardMeta] = []
        for idx, card, locs, name, ml in all_cards:
            print(f"[ICM-Name] Result card {idx}/{total}: {name!r} | locations: {locs!r}")
            out.append((idx, _card_view_href(card), locs, name, ml))
        return out

    matched: list[MatchedCardMeta] = []
    fc = row.get("facility_city") or ""
    fs = row.get("facility_state") or ""
    fst = row.get("facility_street") or ""
    for idx, card, locs, name, ml in all_cards:
        if location_lines_match_facility(locs, fc, fs, fst):
            href = _card_view_href(card)
            print(f"[ICM-Name] Match card {idx}/{total}: {name!r} | {locs!r}")
            matched.append((idx, href, locs, name, ml))
    print(
        f"[ICM-Name] {len(matched)}/{total} cards match facility "
        f"(city={fc!r}, state={fs!r}, street={fst!r}) — opening View on matches only."
    )
    return matched


def _click_view_on_results_page(
    driver,
    card_index: int,
    view_href: Optional[str] = None,
) -> None:
    """Find View link fresh on the results page (never reuse a stored WebElement)."""
    icm.dismiss_overlays_if_any(driver)
    link = None

    if view_href:
        for a in _find_result_view_links(driver):
            try:
                h = (a.get_attribute("href") or "").strip()
            except StaleElementReferenceException:
                continue
            if h == view_href or (view_href in h) or (h in view_href):
                link = a
                break

    if link is None:
        links = _find_result_view_links(driver)
        if 1 <= card_index <= len(links):
            link = links[card_index - 1]
        else:
            live = _find_search_result_cards(driver)
            if card_index > len(live):
                raise TimeoutException(f"Card index {card_index} not on results page")
            link = _card_view_link(live[card_index - 1])

    if link is None:
        raise TimeoutException(f"View link not found for card {card_index}")

    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
        link,
    )
    time.sleep(1.2)


def _collect_from_open_report(
    driver,
    row: dict[str, Any],
    card_index: int,
    card_name: Optional[str],
    card_locs: List[str],
    match_label: Optional[str],
    record_index: int,
) -> NameSearchResultRow:
    base = NameSearchResultRow(
        input_row_index=row.get("input_row_index", 0),
        facility_number=row.get("facility_number"),
        facility_name=row.get("facility_name"),
        facility_street=row.get("facility_street"),
        facility_city=row.get("facility_city"),
        facility_state=row.get("facility_state"),
        owner_name_raw=row.get("owner_name_raw"),
        owner_type="individual",
        icm_search_mode="name",
        search_first_name=row.get("search_first_name"),
        search_last_name=row.get("search_last_name"),
        search_middle=row.get("search_middle"),
        search_city_typed=row.get("search_city_typed"),
        search_state_typed=row.get("search_state_typed"),
        result_card_index=card_index,
        result_card_name=card_name,
        result_card_locations=" | ".join(card_locs) if card_locs else None,
        result_match_label=match_label,
        record_index=record_index,
        extra_source=dict(row.get("extra_source") or {}),
    )

    search_key = _search_dedupe_key(row)
    try:
        _wait_icm_report_page(driver)
        time.sleep(1.2)
        icm.dismiss_overlays_if_any(driver)
        icm_row = icm._collect_current_report_row(
            driver,
            search_key,
            record_index,
            card_name,
        )
        base.owner_from_results = icm_row.owner_from_results
        base.report_name = icm_row.report_name
        base.phone_numbers = icm_row.phone_numbers
        base.emails = icm_row.emails
        base.locations = icm_row.locations
        base.page_url = icm_row.page_url
        base.status = icm_row.status

        report_loc_lines = _split_location_field(base.locations)
        verify_lines = report_loc_lines if report_loc_lines else list(card_locs)
        if not base.locations and card_locs:
            base.locations = " | ".join(card_locs)

        has_phones = bool((base.phone_numbers or "").strip())
        has_emails = bool((base.emails or "").strip())
        has_locs = bool((base.locations or "").strip())

        if VERIFY_LOCATION_ON_REPORT:
            matched = location_lines_match_facility(
                verify_lines,
                row.get("facility_city") or "",
                row.get("facility_state") or "",
                row.get("facility_street") or "",
            )
            base.location_match = matched
            if not matched:
                base.status = "no_location_match"
                base.error = (
                    "Locations do not include facility city/state/street: "
                    f"city={row.get('facility_city')!r} state={row.get('facility_state')!r} "
                    f"street={row.get('facility_street')!r}"
                )
                print(f"[ICM-Name] Card {card_index} ({card_name!r}) location mismatch.")
            elif has_phones or has_emails or has_locs:
                base.status = "ok"
                print(
                    f"[ICM-Name] Card {card_index} ({card_name!r}) OK — "
                    f"phones={has_phones} emails={has_emails} locations={has_locs}"
                )
            else:
                base.status = "no_contact_data"
                base.error = "Location matched but ICM report had no phones/emails/locations scraped"
                print(f"[ICM-Name] Card {card_index} ({card_name!r}) no contact data on report.")
        elif has_phones or has_emails or has_locs:
            base.status = "ok"
        else:
            base.status = "no_contact_data"
            base.error = "No phones, emails, or locations scraped from ICM report"

        if base.report_name and icm._looks_like_icm_section_header(base.report_name) and card_name:
            base.report_name = card_name
    except Exception as e:
        base.status = "extract_error"
        base.error = str(e)
    return base


def _return_to_results(driver, results_url: str) -> None:
    """Reload search results URL so card list DOM is fresh (avoids stale elements)."""
    try:
        u = (driver.current_url or "").lower()
        if "/dashboard/search" in u and _find_result_view_links(driver):
            return
    except Exception:
        pass
    driver.get(results_url)
    time.sleep(0.9)
    icm.dismiss_overlays_if_any(driver)
    _wait_for_name_search_results(
        driver, timeout=min(RETURN_TO_RESULTS_WAIT_SEC, NAME_SEARCH_RESULTS_WAIT_SEC)
    )


def step_collect_all_results(driver, row: dict[str, Any], results_url: str) -> List[NameSearchResultRow]:
    """Open View on location-matching cards only; scrape each report (step 2–3)."""
    cards = _filter_cards_for_facility(driver, row)
    if not cards:
        total = len(_enumerate_result_cards(driver))
        if total:
            print("[ICM-Name] No cards matched facility location — writing no_location_match row.")
            return [
                NameSearchResultRow(
                    input_row_index=row.get("input_row_index", 0),
                    facility_number=row.get("facility_number"),
                    facility_name=row.get("facility_name"),
                    facility_street=row.get("facility_street"),
                    facility_city=row.get("facility_city"),
                    facility_state=row.get("facility_state"),
                    owner_name_raw=row.get("owner_name_raw"),
                    search_first_name=row.get("search_first_name"),
                    search_last_name=row.get("search_last_name"),
                    search_middle=row.get("search_middle"),
                    search_city_typed=row.get("search_city_typed"),
                    search_state_typed=row.get("search_state_typed"),
                    status="no_location_match",
                    error=f"0/{total} result cards matched facility city/state in preview Locations",
                    extra_source=dict(row.get("extra_source") or {}),
                )
            ]
        print("[ICM-Name] No result cards to open — writing no_results row.")
        return [
            NameSearchResultRow(
                input_row_index=row.get("input_row_index", 0),
                facility_number=row.get("facility_number"),
                facility_name=row.get("facility_name"),
                facility_street=row.get("facility_street"),
                facility_city=row.get("facility_city"),
                facility_state=row.get("facility_state"),
                owner_name_raw=row.get("owner_name_raw"),
                search_first_name=row.get("search_first_name"),
                search_last_name=row.get("search_last_name"),
                search_middle=row.get("search_middle"),
                search_city_typed=row.get("search_city_typed"),
                search_state_typed=row.get("search_state_typed"),
                status="no_results",
                error="No result cards on search results page",
                extra_source=dict(row.get("extra_source") or {}),
            )
        ]

    rows_out: List[NameSearchResultRow] = []
    rec = 1
    seen_view_hrefs: set[str] = set()
    for card_index, view_href, locs, name, match_label in cards:
        if SKIP_DUPLICATE_ICM_CARDS and view_href and view_href in seen_view_hrefs:
            print(f"[ICM-Name] Skip duplicate View link for card {card_index} ({name!r})")
            continue
        if view_href:
            seen_view_hrefs.add(view_href)

        last_err: Optional[Exception] = None
        for attempt in range(5):
            try:
                if attempt > 0:
                    print(f"[ICM-Name] Retry card {card_index} attempt {attempt + 1}/5...")
                    _return_to_results(driver, results_url)
                _click_view_on_results_page(driver, card_index, view_href)
                rows_out.append(
                    _collect_from_open_report(
                        driver, row, card_index, name, locs, match_label, rec
                    )
                )
                rec += 1
                _return_to_results(driver, results_url)
                last_err = None
                break
            except StaleElementReferenceException as e:
                last_err = e
                print(f"[ICM-Name] Stale element on card {card_index}; reloading results...")
                _return_to_results(driver, results_url)
                time.sleep(0.5)
            except Exception as e:
                last_err = e
                print(f"[ICM-Name] Card {card_index} failed: {type(e).__name__}: {e}")
                rows_out.append(
                    NameSearchResultRow(
                        input_row_index=row.get("input_row_index", 0),
                        facility_number=row.get("facility_number"),
                        owner_name_raw=row.get("owner_name_raw"),
                        search_first_name=row.get("search_first_name"),
                        search_last_name=row.get("search_last_name"),
                        result_card_index=card_index,
                        result_card_name=name,
                        record_index=rec,
                        status="view_error",
                        error=str(e),
                        extra_source=dict(row.get("extra_source") or {}),
                    )
                )
                rec += 1
                try:
                    _return_to_results(driver, results_url)
                except Exception:
                    pass
                break
        if last_err is not None and isinstance(last_err, StaleElementReferenceException):
            rows_out.append(
                NameSearchResultRow(
                    input_row_index=row.get("input_row_index", 0),
                    facility_number=row.get("facility_number"),
                    owner_name_raw=row.get("owner_name_raw"),
                    search_first_name=row.get("search_first_name"),
                    search_last_name=row.get("search_last_name"),
                    result_card_index=card_index,
                    result_card_name=name,
                    record_index=rec,
                    status="stale_element",
                    error=str(last_err),
                    extra_source=dict(row.get("extra_source") or {}),
                )
            )
            rec += 1
    return rows_out


def _ensure_name_search_dashboard(driver) -> None:
    """Return to main dashboard so Name tab + form work (not report/search results page)."""
    icm.ensure_icm_session(driver)
    try:
        u = (driver.current_url or "").lower()
    except Exception:
        u = ""
    if "/dashboard/search" in u or "/dashboard/reports/" in u or "create-record" in u:
        print("[ICM-Name] Leaving report/results page → dashboard for next search.")
        driver.get(icm.START_URL)
        time.sleep(1.0)
        icm.dismiss_overlays_if_any(driver)
        icm.ensure_icm_session(driver)


def process_one_row(driver, row: dict[str, Any]) -> List[NameSearchResultRow]:
    try:
        print(
            f"[ICM-Name] --- Row {row.get('input_row_index')} "
            f"{row.get('owner_name_raw')} ---"
        )
        _ensure_name_search_dashboard(driver)
        results_url = step_enter_name_and_search(driver, row)
        return step_collect_all_results(driver, row, results_url)
    except TimeoutException as e:
        return [
            NameSearchResultRow(
                input_row_index=row.get("input_row_index", 0),
                owner_name_raw=row.get("owner_name_raw"),
                search_first_name=row.get("search_first_name"),
                search_last_name=row.get("search_last_name"),
                status="timeout",
                error=str(e),
                extra_source=dict(row.get("extra_source") or {}),
            )
        ]
    except Exception as e:
        if looks_like_browser_closed(e):
            raise
        return [
            NameSearchResultRow(
                input_row_index=row.get("input_row_index", 0),
                owner_name_raw=row.get("owner_name_raw"),
                search_first_name=row.get("search_first_name"),
                search_last_name=row.get("search_last_name"),
                status="error",
                error=str(e),
                extra_source=dict(row.get("extra_source") or {}),
            )
        ]


def _flatten_rows_for_excel(rows: List[NameSearchResultRow]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for r in rows:
        d = asdict(r)
        extra = d.pop("extra_source", {}) or {}
        for k, v in extra.items():
            d[f"src_{k}"] = v
        records.append(d)
    return pd.DataFrame(records)


def run_name_search_batch(
    input_xlsx: str | None = None,
    output_xlsx: str | None = None,
    max_rows: int | None = None,
    pause_between: float | None = None,
    quit_driver_when_done: bool = True,
) -> tuple[pd.DataFrame, str]:
    ix = INPUT_XLSX if input_xlsx is None else input_xlsx
    ox = OUTPUT_XLSX if output_xlsx is None else output_xlsx
    mx = MAX_ROWS if max_rows is None else max_rows
    pause = PAUSE_BETWEEN_SEARCHES if pause_between is None else pause_between

    input_limit = globals().get("MAX_INPUT_ROWS")
    all_rows = load_input_rows(ix, limit_rows=input_limit)
    skipped = skipped_rows_as_results(all_rows)
    input_rows = rows_for_name_search(all_rows)
    if mx is not None:
        input_rows = input_rows[: int(mx)]
    n_corp = sum(1 for r in all_rows if r.get("owner_type") == "corporate")
    n_bad = sum(1 for r in all_rows if r.get("owner_type") == "unparseable")

    print(f"[ICM] NY export rows (owner present): {len(all_rows)}")
    print(f"[ICM]   → name search (individuals): {len(input_rows)}")
    print(f"[ICM]   → skip corporate (phone later): {n_corp}")
    print(f"[ICM]   → skip unparseable name: {n_bad}")

    include_skipped = bool(globals().get("INCLUDE_SKIPPED_IN_OUTPUT", False))
    all_out: List[NameSearchResultRow] = list(skipped) if include_skipped else []
    if include_skipped:
        print(f"[ICM] Including {len(skipped)} skipped rows in output Excel.")
    else:
        print("[ICM] Output will include ICM name-search rows only (set INCLUDE_SKIPPED_IN_OUTPUT=True for all skipped).")
    if not input_rows:
        print("[ICM] No individual owners to search. Writing skipped rows only.")
        out_df = _flatten_rows_for_excel(all_out)
        saved = _save_excel(out_df, ox)
        return out_df, saved

    driver = icm.build_driver(headless=HEADLESS)
    cache: dict[str, List[NameSearchResultRow]] = {}

    try:
        icm.ensure_icm_session(driver)
        if DEDUPLICATE_SEARCHES:
            unique_keys: list[str] = []
            key_to_rep: dict[str, dict[str, Any]] = {}
            for row in input_rows:
                k = _search_dedupe_key(row)
                if k not in key_to_rep:
                    key_to_rep[k] = row
                    unique_keys.append(k)
            print(f"[ICM-Name] Unique searches: {len(unique_keys)} (from {len(input_rows)} rows)")
            for n, k in enumerate(unique_keys, start=1):
                rep = key_to_rep[k]
                print(f"[ICM-Name] [{n}/{len(unique_keys)}] unique search {k}")
                cache[k] = process_one_row(driver, rep)
                time.sleep(pause)
            for row in input_rows:
                k = _search_dedupe_key(row)
                for scraped in cache.get(k, []):
                    clone = NameSearchResultRow(**asdict(scraped))
                    clone.input_row_index = row.get("input_row_index", 0)
                    clone.facility_number = row.get("facility_number")
                    clone.facility_name = row.get("facility_name")
                    clone.facility_street = row.get("facility_street")
                    clone.facility_city = row.get("facility_city")
                    clone.facility_state = row.get("facility_state")
                    clone.owner_name_raw = row.get("owner_name_raw")
                    clone.owner_type = "individual"
                    clone.icm_search_mode = "name"
                    clone.extra_source = dict(row.get("extra_source") or {})
                    all_out.append(clone)
        else:
            for n, row in enumerate(input_rows, start=1):
                print(f"[ICM-Name] [{n}/{len(input_rows)}]")
                all_out.extend(process_one_row(driver, row))
                time.sleep(pause)
    finally:
        if quit_driver_when_done:
            try:
                driver.quit()
            except Exception:
                pass

    out_df = _flatten_rows_for_excel(all_out)
    saved = _save_excel(out_df, ox)
    print(f"[ICM-Name] Saved: {saved} ({len(out_df)} rows)")
    return out_df, saved


def run_icm_ny_export(
    input_xlsx: str | None = None,
    output_xlsx: str | None = None,
    max_rows: int | None = None,
) -> tuple[pd.DataFrame, str]:
    """Main entry: ICM name search for NY state export (individual owners only)."""
    return run_name_search_batch(
        input_xlsx=input_xlsx,
        output_xlsx=output_xlsx,
        max_rows=max_rows,
    )


if __name__ == "__main__":
    print("=" * 60)
    print("Instant Checkmate — NY export (individuals, name search)")
    print("Input:", INPUT_XLSX)
    print("Output:", OUTPUT_XLSX)
    print("=" * 60)
    df, path = run_icm_ny_export()
    print("Saved:", path)
    print(df.head(10))
