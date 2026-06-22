#!/usr/bin/env python
# coding: utf-8
"""
Spy Dialer — People search automation (standalone).

Flow:
  1. Read NY State export ``file/ny.csv`` (owner name + facility city/state).
  2. Open https://www.spydialer.com/ → PEOPLE tab → fill first/middle/last/city/state → Search.
  3. On PeopleResult.aspx, click each row's **Details** button.
  4. Scrape phones (and related fields) from each detail page → save Excel after each owner.

Re-run: reads existing output `status` column — skips ok/no_results; retries error/timeout only.

Run (full NY, auto output per partition):
  python3 spy_dialer_people_automation.py --offset 0 --limit 1487
  python3 spy_dialer_people_automation.py --offset 1487 --limit 1487

Bronx only:
  python3 spy_dialer_people_automation.py --city BRONX --offset 0 --limit 118
"""
from __future__ import annotations

import argparse
import shutil
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

try:
    from automation.chrome import build_chrome_driver, headless_default
    from automation.services.browser import looks_like_browser_closed
except ImportError:
    from chrome import build_chrome_driver, headless_default

    def looks_like_browser_closed(exc=None, message=''):
        text = message or str(exc or '').lower()
        return 'invalid session' in text or 'disconnected' in text

_PROJECT_ROOT = Path(__file__).resolve().parent

# =============================================================================
# CONFIG
# =============================================================================
INPUT_CSV = str(_PROJECT_ROOT / "file" / "ny.csv")
INPUT_XLSX = str(_PROJECT_ROOT / "file" / "newOne.xlsx")  # legacy fallback
INPUT_ROCKLAND_XLSX = str(_PROJECT_ROOT / "file" / "Food Facilities - Rockland NY.xlsx")
OUTPUT_DIR = _PROJECT_ROOT / "output"
# Legacy Excel input (only if --input points to .xlsx)
USE_SHEET = "auto_body"

AUTO_BODY_SHEET_NAMES = (
    "Bronx Auto Body Shops",
    "NY-AUTO-REPAIR-SHOP",
    "NY AUTO REPAIR SHOP",
)
BRONX_BUSINESSES_SHEET_NAMES = (
    "Bronx Businesses",
    "Active businesses",
)

# 0-based index: 2 = Excel tab 3 = Bronx Auto Body Shops
INPUT_SHEET_INDEX = 2
INPUT_SHEET_NAME = "Bronx Auto Body Shops"
REQUIRE_SHEET_NAME_MATCH = True
# First individual on auto_body tab should be Antonio (sanity check)
AUTO_BODY_FIRST_OWNER = "ANTONIO ASSALONE"
OUTPUT_XLSX = str(OUTPUT_DIR / "spy_dialer_ny_all.xlsx")

START_URL = "https://www.spydialer.com/"
PEOPLE_RESULTS_PATH = "PeopleResult.aspx"

WAIT_SEC = 25
HEADLESS = headless_default()
PAUSE_BETWEEN_SEARCHES = 2.0
MAX_ROWS: Optional[int] = None  # max individual searches this run; None = all in partition
# Optional: only these Excel row numbers (on Bronx Auto Body Shops: Antonio=2, Randy=3, …)
ONLY_EXCEL_ROWS: Optional[list[int]] = None  # example: [2, 3, 4, 5]
# Optional: start at this Excel row and continue downward (e.g. 2 = ANTONIO ASSALONE on tab 3)
START_EXCEL_ROW: Optional[int] = None
MAX_INPUT_ROWS: Optional[int] = None
INCLUDE_SKIPPED_IN_OUTPUT = False
DEDUPLICATE_SEARCHES = True
# If True, only open Details when result location matches facility city/state
FILTER_RESULTS_BY_LOCATION = False
MAX_DETAIL_RECORDS_PER_SEARCH: Optional[int] = None  # None = every Details button
# Re-run: skip owners already in output with a done status; retry only error statuses.
RESUME_FROM_OUTPUT = True

# status column — done = skip on re-run; retry = search again
STATUS_DONE = frozenset({
    "ok",
    "no_results",
    "no_phones_on_detail",
    "no_matching_records",
    "skipped_corporate",
    "skipped_unparseable",
})
STATUS_RETRY = frozenset({
    "error",
    "timeout",
    "extract_error",
})
# Pause when Spy Dialer fails with a network error; do not pre-check before every row.
PAUSE_ON_NETWORK_ERROR = True
INTERNET_CHECK_INTERVAL_SEC = 15.0
INTERNET_CHECK_TIMEOUT_SEC = 8.0

_COL_OWNER = "Owner Name "
_COL_FACILITY_CITY = "Facility City"
_COL_FACILITY_STATE = "Facility State"
_COL_FACILITY_STREET = "Facility Street"
_COL_FACILITY_ZIP = "Facility Zip Code"
_COL_FACILITY_NUM = "Facility #"
_COL_FACILITY_NAME = "Facility Name"
_COL_OWNER_OVERFLOW = "Owner Name Overflow"

_CORPORATE_RE = re.compile(
    r"\b(corp|corporation|inc|incorporated|llc|l\.l\.c\.|ltd|limited|co\.|company)\b",
    re.IGNORECASE,
)

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
class SpyDialerPeopleRow:
    input_row_index: int = 0
    facility_number: Optional[str] = None
    facility_name: Optional[str] = None
    facility_street: Optional[str] = None
    facility_city: Optional[str] = None
    facility_state: Optional[str] = None
    owner_name_raw: Optional[str] = None
    owner_type: Optional[str] = None
    search_first_name: Optional[str] = None
    search_middle: Optional[str] = None
    search_last_name: Optional[str] = None
    search_city: Optional[str] = None
    search_state: Optional[str] = None
    result_record_index: int = 0
    result_list_name: Optional[str] = None
    result_list_location: Optional[str] = None
    result_list_age: Optional[str] = None
    report_name: Optional[str] = None
    report_age: Optional[str] = None
    phone_numbers: Optional[str] = None
    phone_types: Optional[str] = None
    lives_in: Optional[str] = None
    may_know: Optional[str] = None
    detail_url: Optional[str] = None
    total_hits: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None
    extra_source: dict[str, Any] = field(default_factory=dict)


def _cell_str(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _norm_col_map(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().lower(): c for c in df.columns}


def parse_owner_name(raw: str) -> tuple[str, str, str]:
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


def is_corporate_owner(owner: str, owner_overflow: str = "") -> bool:
    blob = " ".join(_cell_str(x) for x in (owner, owner_overflow) if _cell_str(x))
    return bool(_CORPORATE_RE.search(blob))


def classify_owner(owner: str, owner_overflow: str = "") -> str:
    if is_corporate_owner(owner, owner_overflow):
        return "corporate"
    first, last, _mid = parse_owner_name(owner)
    if not first or not last:
        return "unparseable"
    return "individual"


def state_for_dropdown(state_raw: str) -> str:
    s = _cell_str(state_raw).upper()
    if not s:
        return ""
    if len(s) == 2 and s in US_STATE_TO_NAME:
        return US_STATE_TO_NAME[s]
    return _cell_str(state_raw)


def state_abbrev_for_spydialer(state_raw: str) -> str:
    """Spy Dialer people form uses 2-letter values on DrpDwnLstNmeStt (e.g. NY)."""
    s = _cell_str(state_raw).upper()
    if len(s) == 2 and s in US_STATE_TO_NAME:
        return s
    full = state_for_dropdown(state_raw).upper()
    for abbr, name in US_STATE_TO_NAME.items():
        if name.upper() == full:
            return abbr
    return s[:2] if len(s) >= 2 else s


def _normalize_zip(zip_raw: object) -> str:
    if zip_raw is None or (isinstance(zip_raw, float) and pd.isna(zip_raw)):
        return ""
    if isinstance(zip_raw, float) and zip_raw.is_integer():
        zip_raw = int(zip_raw)
    digits = re.sub(r"\D", "", str(zip_raw).strip())
    return digits[:5] if len(digits) >= 5 else digits


def city_looks_like_state_name(city: str, state: str) -> bool:
    """True when Facility City is really the state (e.g. NEW YORK + NY), not a town."""
    c = _cell_str(city).upper()
    s = _cell_str(state).upper()
    if not c:
        return False
    if c == s or c in US_STATE_TO_NAME:
        return True
    if c == state_for_dropdown(state).upper():
        return True
    # NY export often lists state name in the city column
    if s in ("NY", "NEW YORK") and c == "NEW YORK":
        return True
    return False


def _ny_borough_from_zip(zip_code: str) -> str:
    """Map NYC-area ZIP to borough name for Spy Dialer city field."""
    z = _normalize_zip(zip_code)
    if len(z) != 5 or not z.isdigit():
        return ""
    p3 = int(z[:3])
    if p3 == 104:
        return "Bronx"
    if p3 == 103:
        return "Staten Island"
    if p3 == 112:
        return "Brooklyn"
    if p3 in (110, 111, 113, 114):
        return "Queens"
    if 100 <= p3 <= 102:
        return "Manhattan"
    return ""


def city_for_spydialer_search(
    facility_city: str,
    facility_state: str,
    facility_zip: str = "",
) -> str:
    """
    City typed on Spy Dialer PEOPLE form: real municipality only, never the state name.
    When Facility City is NEW YORK (state placeholder), infer NYC borough from ZIP if possible.
    """
    c = _cell_str(facility_city)
    if not c:
        return ""
    if not city_looks_like_state_name(c, facility_state):
        return c.title()
    st = _cell_str(facility_state).upper()
    if st in ("NY", "NEW YORK") or state_abbrev_for_spydialer(facility_state) == "NY":
        borough = _ny_borough_from_zip(facility_zip)
        if borough:
            return borough
    return ""


def _match_sheet_name(want: str, actual: str) -> bool:
    a = actual.strip().lower()
    w = want.strip().lower()
    return a == w or w in a or a in w


def resolve_input_sheet_for_config(path: str) -> tuple[int, str]:
    """
    Pick worksheet from USE_SHEET (auto_body / bronx_businesses) or INPUT_SHEET_INDEX.
    Matches renamed Calc tabs (e.g. NY-AUTO-REPAIR-SHOP).
    """
    xl = pd.ExcelFile(path)
    names = xl.sheet_names
    mode = (USE_SHEET or "auto_body").strip().lower()

    if mode == "auto_body":
        candidates = list(AUTO_BODY_SHEET_NAMES)
        for want in candidates:
            for i, n in enumerate(names):
                if _match_sheet_name(want, n):
                    return i, n
        for i, n in enumerate(names):
            nl = n.lower()
            if "auto" in nl and ("body" in nl or "repair" in nl or "shop" in nl):
                print(f"[SpyDialer-People] Matched auto-body tab by keyword: {n!r}")
                return i, n
        idx = 2 if len(names) > 2 else max(0, len(names) - 1)
        print(
            f"[SpyDialer-People] No auto-body tab name matched; using index {idx} ({names[idx]!r}). "
            f"All tabs: {names}"
        )
        return idx, names[idx]

    if mode == "bronx_businesses":
        for want in BRONX_BUSINESSES_SHEET_NAMES:
            for i, n in enumerate(names):
                if _match_sheet_name(want, n):
                    return i, n
        idx = 1 if len(names) > 1 else 0
        print(f"[SpyDialer-People] Using Bronx Businesses fallback index {idx} ({names[idx]!r})")
        return idx, names[idx]

    # mode == "index" or unknown
    return resolve_input_sheet(path, sheet_index=INPUT_SHEET_INDEX, sheet_name=INPUT_SHEET_NAME)


def resolve_input_sheet(
    path: str,
    sheet_index: Optional[int] = None,
    sheet_name: Optional[str] = None,
) -> tuple[int, str]:
    """
    Pick worksheet by 0-based index (preferred) or name.
    newOne.xlsx: 0 = full NY export, 1 = Bronx Businesses, 2 = Bronx Auto Body Shops.
    """
    xl = pd.ExcelFile(path)
    names = xl.sheet_names
    if not names:
        raise ValueError(f"No worksheets in {path}")

    if sheet_index is not None:
        idx = int(sheet_index)
        if idx < 0 or idx >= len(names):
            raise ValueError(
                f"Sheet index {idx} out of range for {path}. "
                f"Workbook has {len(names)} sheet(s): {names}"
            )
        resolved = names[idx]
        expected = (sheet_name or INPUT_SHEET_NAME or "").strip()
        if expected and resolved.strip().lower() != expected.lower():
            msg = (
                f"Sheet index {idx} is {resolved!r}, but INPUT_SHEET_NAME is {expected!r}. "
                f"All sheets: {list(enumerate(names))}"
            )
            if REQUIRE_SHEET_NAME_MATCH:
                raise ValueError(msg)
            print(f"[SpyDialer-People] NOTE: {msg} — using tab by index.")
        return idx, resolved

    name = (sheet_name or INPUT_SHEET_NAME or "").strip()
    if not name:
        raise ValueError("Set INPUT_SHEET_INDEX or INPUT_SHEET_NAME")
    for i, n in enumerate(names):
        if n.strip().lower() == name.lower():
            return i, n
    raise ValueError(f"Sheet {name!r} not found in {path}. Available: {names}")


def print_workbook_row_audit(path: str, active_index: Optional[int] = None) -> None:
    """Log every tab's row counts so you can compare with what LibreOffice shows."""
    xl = pd.ExcelFile(path)
    idx = INPUT_SHEET_INDEX if active_index is None else active_index
    print(f"[SpyDialer-People] Workbook audit: {path}")
    print(
        "[SpyDialer-People] pandas reads every stored row in the file "
        "(hidden/filtered rows in Calc still count unless you delete them)."
    )
    for i, name in enumerate(xl.sheet_names):
        df = pd.read_excel(path, sheet_name=i)
        owner_col = None
        for c in df.columns:
            cl = str(c).strip().lower()
            if cl in ("owner name", "owner name ", "owner"):
                owner_col = c
                break
        n_total = len(df)
        if owner_col is not None:
            ser = df[owner_col]
            n_owner = int((ser.notna() & (ser.astype(str).str.strip() != "")).sum())
        else:
            n_owner = n_total
        tag = "  ← INPUT_SHEET_INDEX" if i == idx else ""
        print(
            f"  Excel tab {i + 1} [index {i}] {name!r}: "
            f"{n_total} stored rows, {n_owner} with owner name{tag}"
        )


def _dataframe_to_owner_rows(
    df: pd.DataFrame,
    path_label: str,
    row_index_base: int = 0,
) -> list[dict[str, Any]]:
    """Build owner search rows from a NY export dataframe (csv or xlsx)."""
    cmap = _norm_col_map(df)

    def col(*candidates: str) -> Optional[str]:
        for c in candidates:
            k = c.strip().lower()
            if k in cmap:
                return cmap[k]
        return None

    c_owner = col(_COL_OWNER, "owner name", "owner")
    c_city = col(_COL_FACILITY_CITY, "facility city", "city")
    c_state = col(_COL_FACILITY_STATE, "facility state", "state")
    c_street = col(_COL_FACILITY_STREET, "facility street", "street")
    c_zip = col(_COL_FACILITY_ZIP, "facility zip code", "facility zip", "zip code", "zip")
    c_num = col(_COL_FACILITY_NUM, "facility #", "facility number")
    c_fname = col(_COL_FACILITY_NAME, "facility name")
    c_own_ov = col(_COL_OWNER_OVERFLOW, "owner name overflow")

    if not c_owner:
        raise ValueError(f"Owner column not found in {path_label}. Columns: {list(df.columns)}")

    excel_row_base = 2 + int(row_index_base)
    if START_EXCEL_ROW is not None:
        excel_row_base = int(START_EXCEL_ROW)

    rows: list[dict[str, Any]] = []
    for i, ser in df.iterrows():
        owner = _cell_str(ser.get(c_owner))
        if not owner:
            continue
        ov = _cell_str(ser.get(c_own_ov)) if c_own_ov else ""
        first, last, mid = parse_owner_name(owner)
        city = _cell_str(ser.get(c_city)) if c_city else ""
        state = _cell_str(ser.get(c_state)) if c_state else ""
        zip_raw = ser.get(c_zip) if c_zip else ""
        facility_zip = _normalize_zip(zip_raw)
        search_city = city_for_spydialer_search(city, state, facility_zip)
        otype = classify_owner(owner, ov)
        rows.append(
            {
                "input_row_index": int(i) + excel_row_base,
                "facility_number": _cell_str(ser.get(c_num)) if c_num else "",
                "facility_name": _cell_str(ser.get(c_fname)) if c_fname else "",
                "facility_street": _cell_str(ser.get(c_street)) if c_street else "",
                "facility_city": city,
                "facility_state": state,
                "facility_zip": facility_zip,
                "owner_name_raw": owner,
                "owner_type": otype,
                "search_first_name": first.title() if first else "",
                "search_last_name": last.title() if last else "",
                "search_middle": mid.upper()[:1] if mid else "",
                "search_city": search_city,
                "search_state": state_for_dropdown(state),
                "search_state_abbr": state_abbrev_for_spydialer(state),
                "extra_source": {
                    k: _cell_str(ser.get(cmap[k]))
                    for k in cmap
                    if k not in {
                        (c_owner or "").strip().lower(),
                        (c_city or "").strip().lower(),
                        (c_state or "").strip().lower(),
                    }
                },
            }
        )
    return rows


def _is_xlsx_path(path: str) -> bool:
    return str(path).lower().endswith((".xlsx", ".xlsm", ".xls"))


def is_rockland_food_facilities_df(df: pd.DataFrame) -> bool:
    """NY Rockland County food-facility export (operator first/last columns)."""
    cmap = _norm_col_map(df)
    return (
        "perm. operator first name" in cmap
        and "perm. operator last name" in cmap
        and ("facility city" in cmap or "facility" in cmap)
    )


def _rockland_col(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    cmap = _norm_col_map(df)
    for c in candidates:
        k = re.sub(r"\s+", " ", c.strip().lower())
        if k in cmap:
            return cmap[k]
    for c in candidates:
        k = c.strip().lower()
        for col_key, col_name in cmap.items():
            if k in col_key or col_key in k:
                return col_name
    return None


def load_input_rows_from_rockland_xlsx(
    path: str,
    sheet_index: int = 0,
    limit_rows: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Rockland NY food facilities xlsx → Spy Dialer rows.
    Columns: FACILITY, ADDRESS, FACILITY CITY, PERM. OPERATOR FIRST/LAST NAME, etc.
    """
    df = pd.read_excel(path, sheet_name=sheet_index, dtype=str, keep_default_na=False)
    df = df.rename(columns=lambda c: str(c).strip())
    if not is_rockland_food_facilities_df(df):
        raise ValueError(
            f"Not a Rockland food-facilities workbook: {path}. "
            f"Columns: {list(df.columns)}"
        )

    c_fac = _rockland_col(df, "FACILITY", "facility name")
    c_addr = _rockland_col(df, "ADDRESS", "facility street", "street")
    c_city = _rockland_col(df, "FACILITY CITY", "city")
    c_muni = _rockland_col(df, "FACILITY MUNICIPALITY", "municipality")
    c_county = _rockland_col(df, "COUNTY", "county")
    c_corp = _rockland_col(df, "PERMITTED CORP. NAME", "PERMITTED  CORP. NAME", "corp name")
    c_fn = _rockland_col(df, "PERM. OPERATOR FIRST NAME", "operator first name")
    c_ln = _rockland_col(df, "PERM. OPERATOR LAST NAME", "operator last name")

    if not c_fn or not c_ln:
        raise ValueError(f"Operator name columns not found in {path}. Columns: {list(df.columns)}")

    mapped = pd.DataFrame()
    mapped["Facility Name"] = df[c_fac].map(_cell_str) if c_fac else ""
    mapped["Facility Street"] = df[c_addr].map(_cell_str) if c_addr else ""
    mapped["Facility City"] = df[c_city].map(_cell_str) if c_city else ""
    if c_muni:
        empty_city = mapped["Facility City"].astype(str).str.strip() == ""
        mapped.loc[empty_city, "Facility City"] = df.loc[empty_city, c_muni].map(_cell_str)
    mapped["Facility State"] = "NY"
    mapped["Facility County"] = df[c_county].map(_cell_str) if c_county else "ROCKLAND"
    mapped["Facility #"] = [str(i + 1) for i in range(len(df))]
    # Store corp name for reference only — do not use for owner_type (most sites are LLC/Corp).
    mapped["Permitted Corp Name"] = df[c_corp].map(_cell_str) if c_corp else ""
    mapped["Owner Name Overflow"] = ""
    first = df[c_fn].map(_cell_str)
    last = df[c_ln].map(_cell_str)
    mapped["Owner Name "] = (first + " " + last).str.strip()

    if limit_rows is not None:
        mapped = mapped.head(int(limit_rows))
    print(
        f"[SpyDialer-People] Rockland food facilities: {len(mapped)} rows "
        f"(sheet index {sheet_index})"
    )
    return _dataframe_to_owner_rows(mapped, path, row_index_base=0)


def _input_dataset_tag(path: str) -> str:
    stem = Path(path).stem.lower()
    if "rockland" in stem and "food" in stem:
        return "rockland"
    if Path(path).name.lower() == Path(INPUT_CSV).name.lower():
        return "ny"
    return ""


def _is_csv_path(path: str) -> bool:
    return str(path).lower().endswith(".csv")


def load_input_rows_from_csv(
    path: str,
    filter_city: Optional[str] = None,
    limit_rows: Optional[int] = None,
    csv_row_offset: int = 0,
) -> list[dict[str, Any]]:
    import ny_data as nd

    df = nd.load_ny_dataframe(
        csv_path=path,
        filter_city=filter_city,
        chunk_offset=int(csv_row_offset),
        chunk_size=None,
    )
    if ONLY_EXCEL_ROWS:
        want = {int(r) for r in ONLY_EXCEL_ROWS}
        keep = [i for i in df.index if int(i) + 2 + int(csv_row_offset) in want]
        missing = sorted(want - {int(i) + 2 + int(csv_row_offset) for i in keep})
        if missing:
            print(f"[SpyDialer-People] Warning: CSV rows not found: {missing}")
        df = df.loc[keep].sort_index()
    if START_EXCEL_ROW is not None and int(START_EXCEL_ROW) > 2:
        start_idx = int(START_EXCEL_ROW) - 2 - int(csv_row_offset)
        if start_idx > 0:
            df = df.iloc[start_idx:].reset_index(drop=True)
    if limit_rows is not None:
        df = df.head(int(limit_rows))
    return _dataframe_to_owner_rows(df, path, row_index_base=int(csv_row_offset))


def load_input_rows(
    path: str,
    limit_rows: Optional[int] = None,
    sheet_index: Optional[int] = None,
    sheet_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    idx = INPUT_SHEET_INDEX if sheet_index is None else sheet_index
    sheet_idx, sheet = resolve_input_sheet(
        path,
        sheet_index=idx,
        sheet_name=sheet_name,
    )
    df = pd.read_excel(path, sheet_name=sheet_idx)
    df.attrs["sheet_index"] = sheet_idx
    df.attrs["sheet_name"] = sheet
    if ONLY_EXCEL_ROWS:
        want = {int(r) for r in ONLY_EXCEL_ROWS}
        keep = [i for i in df.index if int(i) + 2 in want]
        missing = sorted(want - {int(i) + 2 for i in keep})
        if missing:
            print(f"[SpyDialer-People] Warning: Excel rows not found on sheet: {missing}")
        df = df.loc[keep].sort_index()
    if START_EXCEL_ROW is not None and int(START_EXCEL_ROW) > 2:
        start_idx = int(START_EXCEL_ROW) - 2
        if start_idx > 0:
            df = df.iloc[start_idx:].reset_index(drop=True)
    if limit_rows is not None:
        df = df.head(int(limit_rows))
    return _dataframe_to_owner_rows(df, path)


def resolve_output_path(
    output_xlsx: str | None,
    individual_offset: int,
    individual_limit: int | None,
    total_individuals: int,
    filter_city: Optional[str] = None,
    dataset_tag: str = "",
) -> str:
    """Unique output file per partition so parallel terminals never overwrite each other."""
    if output_xlsx:
        return output_xlsx
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    city_tag = ""
    if filter_city:
        city_tag = f"_{filter_city.strip().lower().replace(' ', '_')}"
    ds = f"_{dataset_tag}" if dataset_tag else ""
    off = max(0, int(individual_offset))
    if individual_limit is not None or off > 0:
        start = off + 1
        end = min(off + int(individual_limit), total_individuals) if individual_limit else total_individuals
        fname = f"spy_dialer{ds}{city_tag}_ind_{start}_to_{end}.xlsx"
    elif dataset_tag == "rockland":
        fname = "spy_dialer_rockland_ny_all.xlsx"
    else:
        fname = f"spy_dialer{city_tag}_ny_all.xlsx"
    return str(OUTPUT_DIR / fname)


def rows_for_people_search(all_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in all_rows if r.get("owner_type") == "individual"]


def skipped_rows_as_results(all_rows: list[dict[str, Any]]) -> List[SpyDialerPeopleRow]:
    out: List[SpyDialerPeopleRow] = []
    for r in all_rows:
        ot = r.get("owner_type")
        if ot == "individual":
            continue
        out.append(
            SpyDialerPeopleRow(
                input_row_index=r.get("input_row_index", 0),
                facility_number=r.get("facility_number"),
                facility_name=r.get("facility_name"),
                facility_street=r.get("facility_street"),
                facility_city=r.get("facility_city"),
                facility_state=r.get("facility_state"),
                owner_name_raw=r.get("owner_name_raw"),
                owner_type=ot,
                status="skipped_corporate" if ot == "corporate" else "skipped_unparseable",
                extra_source=dict(r.get("extra_source") or {}),
            )
        )
    return out


def _search_dedupe_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            (row.get("search_first_name") or "").lower(),
            (row.get("search_middle") or "").lower(),
            (row.get("search_last_name") or "").lower(),
            (row.get("search_city") or "").lower(),
            (row.get("search_state") or "").lower(),
        ]
    )


def build_driver(headless: bool | None = None) -> webdriver.Chrome:
    return build_chrome_driver(headless=headless if headless is not None else HEADLESS)


def safe_text(el) -> Optional[str]:
    try:
        t = (el.text or "").strip()
        if t:
            return t
        return (el.get_attribute("textContent") or "").strip() or None
    except Exception:
        return None


def dismiss_cookies_if_any(driver) -> None:
    for xp in (
        "//button[contains(., 'Got it')]",
        "//a[contains(., 'Got it')]",
        "//button[contains(., 'Accept')]",
    ):
        try:
            els = driver.find_elements(By.XPATH, xp)
            if els and els[0].is_displayed():
                driver.execute_script("arguments[0].click();", els[0])
                time.sleep(0.3)
                return
        except Exception:
            continue


def _wait_until_not_working(driver, timeout: int = WAIT_SEC) -> None:
    end = time.time() + timeout
    while time.time() < end:
        src = ""
        try:
            src = driver.page_source or ""
        except Exception:
            pass
        if "Spy Dialer is working" not in src and "Spy Dialer is working!" not in src:
            return
        time.sleep(0.4)


def _people_form_visible(driver) -> bool:
    try:
        div = driver.find_element(By.ID, "NameSearchDiv")
        if div.is_displayed():
            return True
        style = (div.get_attribute("style") or "").lower()
        return "display: block" in style or "display:block" in style
    except NoSuchElementException:
        return False


def _show_people_form(driver) -> None:
    """PEOPLE tab is label#ctl00_ContentPlaceHolder1_NameLabel; form is #NameSearchDiv."""
    for eid in ("ctl00_ContentPlaceHolder1_NameLabel", "NameLabel"):
        try:
            tab = driver.find_element(By.ID, eid)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tab)
            driver.execute_script("arguments[0].click();", tab)
            break
        except NoSuchElementException:
            continue

    try:
        WebDriverWait(driver, WAIT_SEC).until(lambda d: _people_form_visible(d))
    except TimeoutException:
        driver.execute_script(
            """
            const phone = document.getElementById('PhoneSearchDiv');
            const people = document.getElementById('NameSearchDiv');
            if (phone) phone.style.display = 'none';
            if (people) people.style.display = 'block';
            const tab = document.getElementById('ctl00_ContentPlaceHolder1_NameLabel');
            if (tab) tab.classList.remove('btn-green-inactive');
            """
        )
        time.sleep(0.4)
    if not _people_form_visible(driver):
        raise RuntimeError("PEOPLE search form (NameSearchDiv) did not become visible")


def _click_people_tab(driver) -> None:
    _show_people_form(driver)
    time.sleep(0.4)


def _find_people_input(driver, element_id: str):
    """Inputs inside NameSearchDiv (TxtBxFrstNm, TxtBxLstNm, …)."""
    root = driver.find_element(By.ID, "NameSearchDiv")
    for eid in (element_id, f"ctl00_ContentPlaceHolder1_{element_id}"):
        try:
            el = root.find_element(By.ID, eid)
            if el.is_enabled():
                return el
        except NoSuchElementException:
            continue
    try:
        return root.find_element(By.ID, element_id)
    except NoSuchElementException:
        return None


def _find_people_state_select(driver):
    root = driver.find_element(By.ID, "NameSearchDiv")
    for eid in ("DrpDwnLstNmeStt", "ctl00_ContentPlaceHolder1_DrpDwnLstNmeStt"):
        try:
            return root.find_element(By.ID, eid)
        except NoSuchElementException:
            continue
    return None


def _set_input_value(driver, el, value: str) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        el.clear()
        el.click()
        el.send_keys(value)
    except Exception:
        pass
    cur = (el.get_attribute("value") or "").strip()
    if cur != value.strip():
        driver.execute_script(
            """
            const el = arguments[0], val = arguments[1];
            el.focus(); el.value = val;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            """,
            el,
            value,
        )


def _select_people_state(driver, state_abbr: str, state_full: str = "") -> None:
    if not state_abbr and not state_full:
        return
    sel_el = _find_people_state_select(driver)
    if sel_el is None:
        print("[SpyDialer-People] Warning: state dropdown DrpDwnLstNmeStt not found")
        return
    sel = Select(sel_el)
    abbr = (state_abbr or "").strip().upper()
    full = (state_full or "").strip()
    tried: list[tuple[str, str]] = []
    if abbr:
        tried.append(("value", abbr))
    if full:
        tried.append(("text", full))
        tried.append(("text", full.upper()))
        tried.append(("text", state_for_dropdown(abbr or full)))
    for kind, val in tried:
        try:
            if kind == "value":
                sel.select_by_value(val)
            else:
                sel.select_by_visible_text(val)
            return
        except Exception:
            continue
    print(f"[SpyDialer-People] Warning: could not select state {abbr or full!r}")


def _click_people_search_button(driver) -> None:
    root = driver.find_element(By.ID, "NameSearchDiv")
    for eid in ("nClck_NmSrchBttn", "ctl00_ContentPlaceHolder1_nClck_NmSrchBttn"):
        try:
            btn = root.find_element(By.ID, eid)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            driver.execute_script("arguments[0].click();", btn)
            return
        except NoSuchElementException:
            continue
    raise RuntimeError("People Search button #nClck_NmSrchBttn not found")


def submit_people_search(driver, row: dict[str, Any]) -> str:
    driver.get(START_URL)
    _wait_until_not_working(driver)
    dismiss_cookies_if_any(driver)
    _click_people_tab(driver)
    time.sleep(0.5)

    first = row.get("search_first_name") or ""
    middle = row.get("search_middle") or ""
    last = row.get("search_last_name") or ""
    city = row.get("search_city") or ""
    state = row.get("search_state") or ""

    f_el = _find_people_input(driver, "TxtBxFrstNm")
    m_el = _find_people_input(driver, "TxtBxMddlNm")
    l_el = _find_people_input(driver, "TxtBxLstNm")
    c_el = _find_people_input(driver, "TxtBxNmCty")

    if not f_el or not l_el:
        raise RuntimeError("People search form: TxtBxFrstNm / TxtBxLstNm not found")

    _set_input_value(driver, f_el, first)
    if m_el and middle:
        _set_input_value(driver, m_el, middle[:1])
    _set_input_value(driver, l_el, last)
    if c_el and city:
        _set_input_value(driver, c_el, city)
    _select_people_state(
        driver,
        row.get("search_state_abbr") or state_abbrev_for_spydialer(state),
        state,
    )

    fc_raw = _cell_str(row.get("facility_city"))
    if fc_raw and city_looks_like_state_name(fc_raw, row.get("facility_state") or ""):
        if city:
            print(
                f"[SpyDialer-People] Facility City {fc_raw!r} is state name; "
                f"using city={city!r} (zip {row.get('facility_zip') or 'n/a'})"
            )
        else:
            print(
                f"[SpyDialer-People] Facility City {fc_raw!r} is state name; "
                "leaving Spy Dialer city blank (state only)"
            )
    print(
        f"[SpyDialer-People] Search: {first} {middle} {last} | {city or '(no city)'} | "
        f"{row.get('search_state_abbr') or state}".strip()
    )
    _click_people_search_button(driver)
    _wait_until_not_working(driver)

    WebDriverWait(driver, WAIT_SEC).until(
        lambda d: PEOPLE_RESULTS_PATH.lower() in (d.current_url or "").lower()
        or len(d.find_elements(By.CSS_SELECTOR, "input[value='Details'][id*='NameViewButton']")) > 0
        or len(d.find_elements(By.ID, "ctl00_ContentPlaceHolder1_TotalHitsLabel")) > 0
        or "Total Hits" in (d.page_source or "")
    )
    time.sleep(0.8)
    return driver.current_url or ""


def _phone_form_visible(driver) -> bool:
    try:
        div = driver.find_element(By.ID, "PhoneSearchDiv")
        if div.is_displayed():
            return True
        style = (div.get_attribute("style") or "").lower()
        return "display: block" in style or "display:block" in style
    except NoSuchElementException:
        return False


def _show_phone_form(driver) -> None:
    for eid in ("ctl00_ContentPlaceHolder1_PhoneLabel", "PhoneLabel"):
        try:
            tab = driver.find_element(By.ID, eid)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tab)
            driver.execute_script("arguments[0].click();", tab)
            break
        except NoSuchElementException:
            continue
    try:
        WebDriverWait(driver, WAIT_SEC).until(lambda d: _phone_form_visible(d))
    except TimeoutException:
        driver.execute_script(
            """
            const phone = document.getElementById('PhoneSearchDiv');
            const people = document.getElementById('NameSearchDiv');
            if (people) people.style.display = 'none';
            if (phone) phone.style.display = 'block';
            """
        )
        time.sleep(0.4)
    if not _phone_form_visible(driver):
        raise RuntimeError("PHONE search form (PhoneSearchDiv) did not become visible")


def _find_phone_input(driver):
    root = driver.find_element(By.ID, "PhoneSearchDiv")
    candidates: list = []
    for eid in (
        "searchinput",
        "atXsACbz",
        "TxtBxPhn",
        "TxtBxPhnNm",
        "ctl00_ContentPlaceHolder1_searchinput",
        "ctl00_ContentPlaceHolder1_atXsACbz",
        "ctl00_ContentPlaceHolder1_TxtBxPhn",
        "ctl00_ContentPlaceHolder1_TxtBxPhnNm",
    ):
        try:
            candidates.append(root.find_element(By.ID, eid))
        except NoSuchElementException:
            continue
    for el in candidates:
        if el.is_displayed() and el.is_enabled():
            return el
    for sel in ("input[type='tel']", "input[type='text']"):
        try:
            el = root.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed():
                return el
        except NoSuchElementException:
            continue
    return candidates[0] if candidates else None


def _click_phone_search_button(driver) -> None:
    root = driver.find_element(By.ID, "PhoneSearchDiv")
    candidates: list = []
    for eid in (
        "IgBtnSch",
        "SearchButton",
        "ctl00_ContentPlaceHolder1_IgBtnSch",
        "ctl00_ContentPlaceHolder1_SearchButton",
        "nClck_PhnSrchBttn",
        "ctl00_ContentPlaceHolder1_nClck_PhnSrchBttn",
    ):
        try:
            candidates.append(root.find_element(By.ID, eid))
        except NoSuchElementException:
            continue
    for btn in candidates:
        if btn.is_displayed():
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            driver.execute_script("arguments[0].click();", btn)
            return
    if candidates:
        btn = candidates[0]
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        return
    raise RuntimeError("Phone Search button not found in PhoneSearchDiv")


def _complete_landline_phone_flow(driver) -> None:
    """After phone search, landlines land on LandlineOptions — pick Name Lookup (free)."""
    url = (driver.current_url or "").lower()
    if "landlineoptions" not in url:
        return
    for eid in ("NameOptionRadioButton", "ctl00_ContentPlaceHolder1_NameOptionRadioButton"):
        try:
            rb = driver.find_element(By.ID, eid)
            driver.execute_script("arguments[0].click();", rb)
            break
        except NoSuchElementException:
            continue
    time.sleep(0.4)
    for eid in ("dfsmljabxdf", "dffsmljabxddf", "dsmljabxf"):
        try:
            btn = driver.find_element(By.ID, eid)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                _wait_until_not_working(driver)
                return
        except NoSuchElementException:
            continue
    raise RuntimeError("Could not submit Name Lookup on LandlineOptions page")


def submit_phone_search(driver, row: dict[str, Any]) -> str:
    phone = row.get("search_phone") or ""
    if not phone:
        raise RuntimeError("Missing search_phone in row")

    driver.get(START_URL)
    _wait_until_not_working(driver)
    dismiss_cookies_if_any(driver)
    _show_phone_form(driver)
    time.sleep(0.5)

    p_el = _find_phone_input(driver)
    if not p_el:
        raise RuntimeError("Phone search input not found")
    _set_input_value(driver, p_el, phone)
    entered = (p_el.get_attribute("value") or "").strip()
    if not entered or re.sub(r"\D", "", entered) != re.sub(r"\D", "", phone):
        raise RuntimeError(f"Phone input did not accept value (wanted {phone!r}, got {entered!r})")
    print(f"[SpyDialer-Phone] Search: {phone}")
    _click_phone_search_button(driver)
    _wait_until_not_working(driver)
    time.sleep(1.0)
    _complete_landline_phone_flow(driver)

    WebDriverWait(driver, WAIT_SEC).until(
        lambda d: "phoneresult" in (d.current_url or "").lower()
        or "PhoneResult" in (d.current_url or "")
        or "landlineoptions" in (d.current_url or "").lower()
        or len(d.find_elements(By.CSS_SELECTOR, ".phonelink")) > 0
        or "Total Hits" in (d.page_source or "")
        or "Name Lookup" in (d.page_source or "")
        or "You entered" in (d.page_source or "")
    )
    time.sleep(0.8)
    return driver.current_url or ""


def process_one_phone(driver, row: dict[str, Any]) -> List[SpyDialerPeopleRow]:
    try:
        print(
            f"[SpyDialer-Phone] --- Row {row.get('input_row_index')} "
            f"{row.get('search_phone')} ---"
        )
        submit_phone_search(driver, row)
        hits = _total_hits_text(driver)
        out = SpyDialerPeopleRow(
            input_row_index=row.get("input_row_index", 0),
            owner_name_raw=row.get("search_phone"),
            search_first_name=row.get("search_first_name"),
            search_last_name=row.get("search_last_name"),
            total_hits=hits,
            status="ok",
            extra_source=dict(row.get("extra_source") or {}),
        )
        phones: List[str] = []
        types: List[str] = []
        for pl in driver.find_elements(By.CSS_SELECTOR, ".phonelink"):
            num = safe_text(pl)
            if num and re.search(r"\d{3}", num):
                phones.append(num)
                try:
                    typ = safe_text(pl.find_element(By.CSS_SELECTOR, ".phonelinktext"))
                    types.append(typ or "")
                except Exception:
                    types.append("")
        if phones:
            out.phone_numbers = " | ".join(phones)
            out.phone_types = " | ".join(t for t in types if t) or None
        else:
            m = re.search(r"(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})", driver.page_source or "")
            if m:
                out.phone_numbers = m.group(1)
            else:
                out.status = "no_results"
        return [out]
    except TimeoutException as e:
        return [
            SpyDialerPeopleRow(
                input_row_index=row.get("input_row_index", 0),
                owner_name_raw=row.get("search_phone"),
                status="timeout",
                error=str(e),
                extra_source=dict(row.get("extra_source") or {}),
            )
        ]
    except Exception as e:
        if looks_like_browser_closed(e):
            raise
        return [
            SpyDialerPeopleRow(
                input_row_index=row.get("input_row_index", 0),
                owner_name_raw=row.get("search_phone"),
                status="error",
                error=str(e),
                extra_source=dict(row.get("extra_source") or {}),
            )
        ]


def process_one_row(
    driver,
    row: dict[str, Any],
    *,
    search_mode: str = "people",
) -> List[SpyDialerPeopleRow]:
    if search_mode == "phone":
        return process_one_phone(driver, row)
    return process_one_owner(driver, row)


def _total_hits_text(driver) -> Optional[str]:
    for eid in ("ctl00_ContentPlaceHolder1_TotalHitsLabel",):
        try:
            el = driver.find_element(By.ID, eid)
            return safe_text(el)
        except NoSuchElementException:
            pass
    if "Total Hits" in (driver.page_source or ""):
        m = re.search(r"Total\s+Hits:\s*(\d+)", driver.page_source, re.I)
        if m:
            return m.group(1)
    return None


def list_details_buttons(driver):
    return driver.find_elements(
        By.CSS_SELECTOR,
        "input[type='submit'][value='Details'][id*='PeopleSearchRepeater'][id*='NameViewButton']",
    )


def _result_row_meta(driver, index: int) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort name/location/age from results list row before opening Details."""
    name = loc = age = None
    try:
        rows = driver.find_elements(
            By.XPATH,
            "//table//tr[.//input[contains(@id,'NameViewButton')]]",
        )
        if index < len(rows):
            tr = rows[index]
            links = tr.find_elements(By.XPATH, ".//a[contains(@id,'NameLink') or contains(@class,'bluelink')]")
            if links:
                name = safe_text(links[0])
            tds = tr.find_elements(By.TAG_NAME, "td")
            chunks = [safe_text(td) for td in tds if safe_text(td)]
            for c in chunks or []:
                if c and re.search(r"\d{2}s", c):
                    age = c
                if c and "," in c and re.search(r"\b[A-Z]{2}\b", c):
                    loc = c
    except Exception:
        pass
    return name, loc, age


def _location_matches_facility(result_loc: str, row: dict[str, Any]) -> bool:
    if not result_loc:
        return True
    blob = result_loc.lower()
    st = _cell_str(row.get("facility_state")).upper()
    city = _cell_str(row.get("facility_city")).lower()
    st_name = state_for_dropdown(st).lower()
    if st and st.lower() not in blob and st_name not in blob:
        return False
    if city and city not in blob:
        for part in re.split(r"[\s,]+", city):
            if len(part) >= 4 and part in blob:
                break
        else:
            return False
    return True


def click_back_to_results(driver) -> None:
    for xp in (
        "//a[contains(., 'Back to Results')]",
        "//input[@value='Back to Results']",
        "//button[contains(., 'Back to Results')]",
    ):
        els = driver.find_elements(By.XPATH, xp)
        if els:
            driver.execute_script("arguments[0].click();", els[0])
            time.sleep(0.5)
            return
    driver.back()
    time.sleep(0.5)


def wait_for_people_results(driver) -> None:
    WebDriverWait(driver, WAIT_SEC).until(
        lambda d: len(list_details_buttons(d)) > 0
        or "PeopleResult" in (d.current_url or "")
        or len(d.find_elements(By.CSS_SELECTOR, ".result-details")) > 0
    )
    time.sleep(0.4)


def extract_detail_page(driver, row: dict[str, Any], record_index: int) -> SpyDialerPeopleRow:
    out = SpyDialerPeopleRow(
        input_row_index=row.get("input_row_index", 0),
        facility_number=row.get("facility_number"),
        facility_name=row.get("facility_name"),
        facility_street=row.get("facility_street"),
        facility_city=row.get("facility_city"),
        facility_state=row.get("facility_state"),
        owner_name_raw=row.get("owner_name_raw"),
        owner_type="individual",
        search_first_name=row.get("search_first_name"),
        search_middle=row.get("search_middle"),
        search_last_name=row.get("search_last_name"),
        search_city=row.get("search_city"),
        search_state=row.get("search_state"),
        result_record_index=record_index,
        detail_url=driver.current_url,
        extra_source=dict(row.get("extra_source") or {}),
    )

    root = None
    try:
        root = driver.find_element(By.CSS_SELECTOR, ".result-details")
    except NoSuchElementException:
        root = driver

    def txt_id(eid: str) -> Optional[str]:
        for scope in (root, driver):
            try:
                el = scope.find_element(By.ID, eid)
                return safe_text(el)
            except NoSuchElementException:
                continue
        return None

    out.report_name = txt_id("ctl00_ContentPlaceHolder1_NameResultLabel")
    age_raw = txt_id("ctl00_ContentPlaceHolder1_AgeLabel")
    out.report_age = (age_raw or "").strip(", ")

    phones: List[str] = []
    types: List[str] = []
    for pl in root.find_elements(By.CSS_SELECTOR, ".phonelink"):
        num = typ = None
        try:
            a = pl.find_element(By.CSS_SELECTOR, "a.bluelink")
            num = safe_text(a)
        except NoSuchElementException:
            pass
        try:
            typ = safe_text(pl.find_element(By.CSS_SELECTOR, ".phonelinktext"))
        except NoSuchElementException:
            pass
        if num:
            phones.append(num)
            types.append(typ or "")

    if not phones:
        for a in root.find_elements(By.CSS_SELECTOR, "a.bluelink"):
            t = safe_text(a)
            if t and re.search(r"\d{3}[-.\s]?\d{3}[-.\s]?\d{4}", t):
                phones.append(t)

    out.phone_numbers = " | ".join(phones) if phones else None
    out.phone_types = " | ".join(types) if types else None

    city_state = txt_id("ctl00_ContentPlaceHolder1_LocationLabel")
    loc_year = txt_id("ctl00_ContentPlaceHolder1_LocationYearLabel")
    out.lives_in = " ".join(x for x in (city_state, loc_year) if x)

    may_lines: List[str] = []
    for block in root.find_elements(By.CSS_SELECTOR, ".other-data > div"):
        t = safe_text(block)
        if t and "may know" not in t.lower():
            may_lines.append(t)
    out.may_know = " | ".join(may_lines) if may_lines else None
    return out


def collect_all_details(driver, row: dict[str, Any], results_url: str) -> List[SpyDialerPeopleRow]:
    wait_for_people_results(driver)
    hits = _total_hits_text(driver)
    buttons = list_details_buttons(driver)
    n = len(buttons)
    print(f"[SpyDialer-People] Results page: {n} Details button(s), hits={hits!r}")

    if n == 0:
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".result-details, #ctl00_ContentPlaceHolder1_NameResultLabel")
                )
            )
            one = extract_detail_page(driver, row, 1)
            one.total_hits = hits
            one.status = "ok" if one.phone_numbers else "no_phones_on_detail"
            return [one]
        except Exception as e:
            return [
                SpyDialerPeopleRow(
                    input_row_index=row.get("input_row_index", 0),
                    owner_name_raw=row.get("owner_name_raw"),
                    search_first_name=row.get("search_first_name"),
                    search_last_name=row.get("search_last_name"),
                    total_hits=hits,
                    status="no_results",
                    error=str(e),
                    extra_source=dict(row.get("extra_source") or {}),
                )
            ]

    cap = MAX_DETAIL_RECORDS_PER_SEARCH
    if cap is not None:
        n = min(n, int(cap))

    rows_out: List[SpyDialerPeopleRow] = []
    for i in range(n):
        wait_for_people_results(driver)
        btns = list_details_buttons(driver)
        if i >= len(btns):
            break

        list_name, list_loc, list_age = _result_row_meta(driver, i)
        if FILTER_RESULTS_BY_LOCATION and list_loc and not _location_matches_facility(list_loc, row):
            print(f"[SpyDialer-People] Skip record {i + 1} (location): {list_loc!r}")
            continue

        btn = btns[i]
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        WebDriverWait(driver, WAIT_SEC).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".result-details, #ctl00_ContentPlaceHolder1_NameResultLabel")
            )
        )
        time.sleep(0.5)
        try:
            rec = extract_detail_page(driver, row, i + 1)
            rec.total_hits = hits
            rec.result_list_name = list_name
            rec.result_list_location = list_loc
            rec.result_list_age = list_age
            if not rec.phone_numbers:
                rec.status = "no_phones_on_detail"
            rows_out.append(rec)
            print(
                f"[SpyDialer-People]   Record {i + 1}: {rec.report_name!r} "
                f"phones={rec.phone_numbers!r}"
            )
        except Exception as e:
            rows_out.append(
                SpyDialerPeopleRow(
                    input_row_index=row.get("input_row_index", 0),
                    owner_name_raw=row.get("owner_name_raw"),
                    search_first_name=row.get("search_first_name"),
                    search_last_name=row.get("search_last_name"),
                    result_record_index=i + 1,
                    result_list_name=list_name,
                    result_list_location=list_loc,
                    total_hits=hits,
                    status="extract_error",
                    error=str(e),
                    extra_source=dict(row.get("extra_source") or {}),
                )
            )

        if i < n - 1:
            click_back_to_results(driver)
            if results_url and PEOPLE_RESULTS_PATH.lower() not in (driver.current_url or "").lower():
                driver.get(results_url)
            wait_for_people_results(driver)
            time.sleep(0.4)

    if not rows_out:
        return [
            SpyDialerPeopleRow(
                input_row_index=row.get("input_row_index", 0),
                owner_name_raw=row.get("owner_name_raw"),
                search_first_name=row.get("search_first_name"),
                search_last_name=row.get("search_last_name"),
                total_hits=hits,
                status="no_matching_records",
                error="No Details opened (filter or empty list)",
                extra_source=dict(row.get("extra_source") or {}),
            )
        ]
    return rows_out


def process_one_owner(driver, row: dict[str, Any]) -> List[SpyDialerPeopleRow]:
    try:
        print(
            f"[SpyDialer-People] --- Row {row.get('input_row_index')} "
            f"{row.get('owner_name_raw')} ---"
        )
        results_url = submit_people_search(driver, row)
        return collect_all_details(driver, row, results_url)
    except TimeoutException as e:
        return [
            SpyDialerPeopleRow(
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
            SpyDialerPeopleRow(
                input_row_index=row.get("input_row_index", 0),
                owner_name_raw=row.get("owner_name_raw"),
                search_first_name=row.get("search_first_name"),
                search_last_name=row.get("search_last_name"),
                status="error",
                error=str(e),
                extra_source=dict(row.get("extra_source") or {}),
            )
        ]


def _flatten_rows_for_excel(rows: List[SpyDialerPeopleRow]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for r in rows:
        d = asdict(r)
        extra = d.pop("extra_source", {}) or {}
        for k, v in extra.items():
            d[f"src_{k}"] = v
        records.append(d)
    return pd.DataFrame(records)


def _save_excel(df: pd.DataFrame, path: str) -> str:
    try:
        df.to_excel(path, index=False)
        return path
    except PermissionError:
        p = Path(path)
        alt = p.with_name(f"{p.stem}_{datetime.now():%Y%m%d_%H%M%S}{p.suffix}")
        df.to_excel(str(alt), index=False)
        print(f"Could not write {path} (file open?). Saved to: {alt}")
        return str(alt)


def load_existing_output(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        return pd.DataFrame()
    try:
        return pd.read_excel(p, dtype=str, keep_default_na=False)
    except Exception as e:
        print(f"[SpyDialer-People] Could not read existing output {path}: {e}")
        return pd.DataFrame()


def _normalize_row_index(val: object) -> int:
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return 0


def _normalize_status(val: object) -> str:
    s = str(val or "").strip().lower()
    if s == "no_result":
        return "no_results"
    return s


def _normalize_facility_number(val: object) -> str:
    digits = re.sub(r"\D", "", str(val or "").strip())
    return digits.lstrip("0") or digits


def _owner_rows(existing: pd.DataFrame, owner_idx: int) -> pd.DataFrame:
    if existing.empty or "input_row_index" not in existing.columns:
        return pd.DataFrame()
    mask = existing["input_row_index"].apply(_normalize_row_index) == int(owner_idx)
    return existing.loc[mask].copy()


def _statuses_for_owner(existing: pd.DataFrame, owner_idx: int) -> set[str]:
    sub = _owner_rows(existing, owner_idx)
    if sub.empty or "status" not in sub.columns:
        return set()
    return {_normalize_status(s) for s in sub["status"].tolist() if str(s).strip()}


def _owner_has_phones_in_output(existing: pd.DataFrame, owner_idx: int) -> bool:
    sub = _owner_rows(existing, owner_idx)
    if sub.empty or "phone_numbers" not in sub.columns:
        return False
    for v in sub["phone_numbers"].tolist():
        if re.search(r"\d{3}", str(v or "")):
            return True
    return False


def owner_output_is_done(existing: pd.DataFrame, owner_idx: int) -> bool:
    """
    Skip re-search when this owner already has a good result in output.
    Any row with status=ok OR any saved phone numbers → done (even if old error rows exist).
    """
    sts = _statuses_for_owner(existing, owner_idx)
    if not sts and not _owner_has_phones_in_output(existing, owner_idx):
        return False
    if "ok" in sts:
        return True
    if _owner_has_phones_in_output(existing, owner_idx):
        return True
    if sts & STATUS_RETRY:
        return False
    return bool(sts) and sts.issubset(STATUS_DONE)


def _done_keys_from_output(existing: pd.DataFrame) -> tuple[set[int], set[str]]:
    """Owners already complete: by input_row_index and facility #."""
    done_ids: set[int] = set()
    done_facilities: set[str] = set()
    if existing.empty or "input_row_index" not in existing.columns:
        return done_ids, done_facilities

    for owner_idx in existing["input_row_index"].apply(_normalize_row_index).unique():
        if owner_idx <= 0:
            continue
        if owner_output_is_done(existing, owner_idx):
            done_ids.add(owner_idx)
            sub = _owner_rows(existing, owner_idx)
            if "facility_number" in sub.columns:
                for f in sub["facility_number"].tolist():
                    fn = _normalize_facility_number(f)
                    if fn:
                        done_facilities.add(fn)
    return done_ids, done_facilities


def _host_reachable(host: str, port: int = 443) -> bool:
    try:
        socket.create_connection((host, port), timeout=INTERNET_CHECK_TIMEOUT_SEC)
        return True
    except OSError:
        return False


def is_internet_up() -> bool:
    """
    Best-effort connectivity (any check passing = online).
    Avoids false 'offline' on Mac/VPN/corporate networks that block 8.8.8.8:53.
    """
    if _host_reachable("www.spydialer.com", 443):
        return True
    if _host_reachable("1.1.1.1", 443):
        return True
    if _host_reachable("8.8.8.8", 53):
        return True
    try:
        socket.getaddrinfo("www.spydialer.com", 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        pass
    try:
        req = Request(
            "https://www.spydialer.com/",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; SpyDialer automation)"},
        )
        with urlopen(req, timeout=INTERNET_CHECK_TIMEOUT_SEC) as resp:
            return resp.status < 500
    except (URLError, OSError, TimeoutError, ValueError):
        pass
    return False


def wait_for_internet(label: str = "") -> None:
    """Block until connectivity checks pass (only after a real network error)."""
    if is_internet_up():
        return
    tag = f" ({label})" if label else ""
    print(
        f"[SpyDialer-People] Network unreachable{tag} — pausing "
        f"(checking every {INTERNET_CHECK_INTERVAL_SEC:.0f}s). "
        "Use --no-internet-wait to disable."
    )
    while not is_internet_up():
        time.sleep(INTERNET_CHECK_INTERVAL_SEC)
    print("[SpyDialer-People] Connectivity OK — resuming searches.")


_NETWORK_ERROR_HINTS = (
    "net::",
    "err_internet",
    "err_connection",
    "err_name_not_resolved",
    "err_network",
    "internet disconnected",
    "no internet",
    "network is unreachable",
    "failed to establish a new connection",
    "connection refused",
    "connection reset",
    "temporary failure in name resolution",
)


def _looks_like_network_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _NETWORK_ERROR_HINTS)


def process_one_owner_with_network(
    driver,
    row: dict[str, Any],
    *,
    pause_on_network_error: bool = True,
) -> List[SpyDialerPeopleRow]:
    """Run one search; on browser network failure optionally wait and retry once."""
    try:
        return process_one_owner(driver, row)
    except (TimeoutException, WebDriverException) as e:
        if looks_like_browser_closed(e):
            raise
        if pause_on_network_error and _looks_like_network_error(e):
            print(f"[SpyDialer-People] Browser network error: {e}")
            wait_for_internet(row.get("owner_name_raw") or "")
            return process_one_owner(driver, row)
        raise


def _backup_output_before_run(output_path: str) -> Optional[str]:
    """Snapshot output xlsx before a resume run (safe if script is interrupted)."""
    p = Path(output_path)
    if not p.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = p.with_name(f"{p.stem}_backup_{stamp}{p.suffix}")
    shutil.copy2(p, dest)
    return str(dest)


def partition_for_resume(
    input_rows: list[dict[str, Any]],
    existing: pd.DataFrame,
) -> tuple[list[dict[str, Any]], pd.DataFrame, dict[str, int]]:
    """
    Split partition into rows to search vs rows already saved in output.
    Skip owners with status=ok or saved phone numbers.
    Re-run only: not in output yet, or only error/timeout/extract_error (no ok).
    """
    stats = {"done": 0, "retry": 0, "new": 0}
    if existing.empty:
        stats["new"] = len(input_rows)
        return list(input_rows), pd.DataFrame(), stats

    done_ids, done_facilities = _done_keys_from_output(existing)
    seen_ids: set[int] = set()
    if "input_row_index" in existing.columns:
        seen_ids = {
            i for i in existing["input_row_index"].apply(_normalize_row_index).unique() if i > 0
        }

    to_run: list[dict[str, Any]] = []
    to_run_ids: set[int] = set()
    to_run_facs: set[str] = set()
    for row in input_rows:
        oid = int(row.get("input_row_index", 0))
        fac = _normalize_facility_number(row.get("facility_number"))
        if oid in done_ids or (fac and fac in done_facilities):
            stats["done"] += 1
            continue
        to_run.append(row)
        to_run_ids.add(oid)
        if fac:
            to_run_facs.add(fac)
        if oid in seen_ids:
            stats["retry"] += 1
        else:
            stats["new"] += 1

    # Keep the FULL file in memory — only replace one owner at a time in _merge_owner_results.
    # Never bulk-delete rows for pending retries (would lose data if the run stops early).
    kept = existing.copy()
    stats["kept_rows"] = len(kept)
    stats["pending_replace_owners"] = len(to_run_ids)

    return to_run, kept, stats


def _merge_owner_results(
    working: pd.DataFrame,
    owner_idx: int,
    new_rows: List[SpyDialerPeopleRow],
    facility_number: Optional[str] = None,
) -> pd.DataFrame:
    """Replace prior output rows for this owner only (after a successful new search)."""
    new_df = _flatten_rows_for_excel(new_rows)
    if working.empty:
        return new_df
    if "input_row_index" not in working.columns:
        return pd.concat([working, new_df], ignore_index=True)
    drop = working["input_row_index"].apply(_normalize_row_index) == int(owner_idx)
    fac = _normalize_facility_number(facility_number)
    if fac and "facility_number" in working.columns:
        drop = drop | (
            working["facility_number"].apply(_normalize_facility_number) == fac
        )
    return pd.concat([working.loc[~drop], new_df], ignore_index=True)


def run_spy_dialer_people_batch(
    input_path: str | None = None,
    output_xlsx: str | None = None,
    max_rows: int | None = None,
    individual_offset: int = 0,
    individual_limit: int | None = None,
    filter_city: Optional[str] = None,
    pause_between: float | None = None,
    quit_driver_when_done: bool = True,
    dry_run: bool = False,
    resume_from_output: bool = True,
    pause_on_network_error: bool = True,
    sheet_index: Optional[int] = None,
    headless: bool | None = None,
) -> tuple[pd.DataFrame, str]:
    ix = INPUT_CSV if input_path is None else input_path
    mx = MAX_ROWS if max_rows is None else max_rows
    pause = PAUSE_BETWEEN_SEARCHES if pause_between is None else pause_between
    dataset_tag = _input_dataset_tag(ix)

    if _is_csv_path(ix):
        print(f"[SpyDialer-People] Input: {ix} (NY State CSV)")
        if filter_city:
            print(f"[SpyDialer-People] City filter: {filter_city.upper()}")
        df_preview = None
        try:
            import ny_data as nd

            df_preview = nd.load_ny_dataframe(csv_path=ix, filter_city=filter_city)
            print(f"[SpyDialer-People] CSV rows after filter: {len(df_preview)}")
        except Exception:
            pass
        all_rows = load_input_rows_from_csv(
            ix,
            filter_city=filter_city,
            limit_rows=MAX_INPUT_ROWS,
        )
        sheet_idx, sheet = None, None
    elif _is_xlsx_path(ix):
        peek = pd.read_excel(ix, sheet_name=sheet_index or 0, nrows=3, dtype=str, keep_default_na=False)
        if is_rockland_food_facilities_df(peek):
            sheet_idx = 0 if sheet_index is None else int(sheet_index)
            print(f"[SpyDialer-People] Input: {ix} (Rockland food facilities, sheet {sheet_idx})")
            all_rows = load_input_rows_from_rockland_xlsx(
                ix,
                sheet_index=sheet_idx,
                limit_rows=MAX_INPUT_ROWS,
            )
            dataset_tag = "rockland"
            sheet = pd.ExcelFile(ix).sheet_names[sheet_idx]
        else:
            sheet_idx, sheet = resolve_input_sheet_for_config(ix)
            print_workbook_row_audit(ix, active_index=sheet_idx)
            all_rows = load_input_rows(
                ix,
                limit_rows=MAX_INPUT_ROWS,
                sheet_index=sheet_idx if sheet_index is None else sheet_index,
                sheet_name=sheet,
            )
    else:
        sheet_idx, sheet = resolve_input_sheet_for_config(ix)
        print_workbook_row_audit(ix, active_index=sheet_idx)
        all_rows = load_input_rows(
            ix,
            limit_rows=MAX_INPUT_ROWS,
            sheet_index=sheet_idx,
            sheet_name=sheet,
        )

    skipped = skipped_rows_as_results(all_rows)
    all_individuals = rows_for_people_search(all_rows)
    off = max(0, int(individual_offset))
    if off:
        input_rows = all_individuals[off:]
    else:
        input_rows = list(all_individuals)
    if individual_limit is not None:
        input_rows = input_rows[: int(individual_limit)]
    elif mx is not None:
        input_rows = input_rows[: int(mx)]

    ox = resolve_output_path(
        output_xlsx,
        individual_offset=off,
        individual_limit=individual_limit if individual_limit is not None else mx,
        total_individuals=len(all_individuals),
        filter_city=filter_city,
        dataset_tag=dataset_tag,
    )

    partition_size = len(input_rows)
    existing_df = pd.DataFrame()
    kept_df = pd.DataFrame()
    resume_stats = {"done": 0, "retry": 0, "new": 0}
    if resume_from_output:
        existing_df = load_existing_output(ox)
        if not existing_df.empty:
            input_rows, kept_df, resume_stats = partition_for_resume(input_rows, existing_df)
            if dry_run:
                print(
                    f"[SpyDialer-People] Resume (dry-run): "
                    f"{resume_stats['done']} skip, {resume_stats['retry']} retry, "
                    f"{resume_stats['new']} new → would search {len(input_rows)}"
                )
            else:
                print(
                    f"[SpyDialer-People] Resume from {ox}: "
                    f"{len(existing_df)} rows kept in file; "
                    f"replace each owner only after its new search finishes "
                    f"({resume_stats.get('pending_replace_owners', len(input_rows))} owners queued)"
                )
                print(
                    f"[SpyDialer-People] Owners: {resume_stats['done']} skip, "
                    f"{resume_stats['retry']} retry, {resume_stats['new']} new "
                    f"→ search {len(input_rows)} this run"
                )
                print(
                    f"[SpyDialer-People] Retry statuses: {', '.join(sorted(STATUS_RETRY))}"
                )

    n_corp = sum(1 for r in all_rows if r.get("owner_type") == "corporate")
    n_bad = sum(1 for r in all_rows if r.get("owner_type") == "unparseable")
    print(f"[SpyDialer-People] File: {ix}")
    if sheet_idx is not None and dataset_tag != "rockland":
        print(
            f"[SpyDialer-People] USE_SHEET={USE_SHEET!r} → "
            f"tab {sheet_idx + 1} [index {sheet_idx}] {sheet!r}"
        )
        if sheet_idx == 0:
            print(
                "[SpyDialer-People] WARNING: tab 1 is the full NY export. "
                "Set USE_SHEET = 'auto_body' for Antonio/Randy list."
            )
        elif sheet_idx == 1 and len(all_rows) > 500:
            print(
                "[SpyDialer-People] WARNING: tab 2 looks like Bronx Businesses (~1500 rows). "
                "Your screenshot (Antonio row 2) is USE_SHEET='auto_body' (tab 3 / Bronx Auto Body Shops)."
            )
    if START_EXCEL_ROW is not None:
        print(f"[SpyDialer-People] Starting at Excel row: {START_EXCEL_ROW}")
    print(f"[SpyDialer-People] Input rows (owners): {len(all_rows)}")
    preview_all = rows_for_people_search(all_rows)[:12]
    if (
        preview_all
        and (USE_SHEET or "").strip().lower() == "auto_body"
        and not _is_csv_path(ix)
        and dataset_tag != "rockland"
    ):
        first_owner = (preview_all[0].get("owner_name_raw") or "").upper()
        if AUTO_BODY_FIRST_OWNER.upper() not in first_owner:
            print(
                "[SpyDialer-People] ERROR: Wrong sheet for auto_body — first person is "
                f"{preview_all[0].get('owner_name_raw')!r}, expected {AUTO_BODY_FIRST_OWNER!r}. "
                "In Calc open tab 3 (Bronx Auto Body Shops / NY-AUTO-REPAIR-SHOP), "
                "or set USE_SHEET='bronx_businesses' if you want the large list."
            )
    if preview_all:
        print("[SpyDialer-People] Individuals on this sheet (file order, first 12):")
        for r in preview_all:
            print(
                f"  excel~{r.get('input_row_index')} | #{r.get('facility_number')} | "
                f"{r.get('owner_name_raw')} | {r.get('facility_street')} | {r.get('search_city')}"
            )
    if input_rows:
        print(
            f"[SpyDialer-People] THIS RUN will search ({len(input_rows)} owner(s), "
            f"MAX_ROWS={mx if mx is not None else 'all'}):"
        )
        show = input_rows[:12]
        for r in show:
            print(
                f"  excel~{r.get('input_row_index')} | #{r.get('facility_number')} | "
                f"{r.get('owner_name_raw')} | {r.get('search_city')}, {r.get('search_state_abbr')}"
            )
        if len(input_rows) > 12:
            print(f"  … and {len(input_rows) - 12} more")
    elif resume_stats.get("done") and not dry_run:
        print("[SpyDialer-People] THIS RUN: nothing to search (partition complete, no errors).")
    elif mx is not None:
        print(
            f"[SpyDialer-People] MAX_ROWS={mx} — increase it or set START_EXCEL_ROW=2 "
            "on Bronx Auto Body Shops (INPUT_SHEET_INDEX = 2) for ANTONIO ASSALONE."
        )
    print(f"[SpyDialer-People]   → individuals on sheet: {len(all_individuals)}")
    if off or individual_limit is not None:
        end = off + partition_size
        print(
            f"[SpyDialer-People]   → this partition: individuals {off + 1}–{end} "
            f"of {len(all_individuals)} (offset={off}, limit={individual_limit!r})"
        )
    print(f"[SpyDialer-People]   → skip corporate: {n_corp}")
    print(f"[SpyDialer-People]   → skip unparseable: {n_bad}")
    print(f"[SpyDialer-People]   → output file: {ox}")

    all_out: List[SpyDialerPeopleRow] = []
    if INCLUDE_SKIPPED_IN_OUTPUT:
        all_out.extend(skipped)

    if not input_rows:
        if not kept_df.empty:
            saved = _save_excel(kept_df, ox)
            print(f"[SpyDialer-People] Nothing to search (all done / no errors). Kept: {saved}")
            return kept_df, saved
        out_df = _flatten_rows_for_excel(all_out)
        saved = _save_excel(out_df, ox)
        print(f"[SpyDialer-People] No individuals to search. Saved: {saved}")
        return out_df, saved

    if dry_run:
        print("[SpyDialer-People] DRY RUN — no browser; partition looks OK.")
        return pd.DataFrame(), ox

    if resume_from_output and not existing_df.empty and input_rows:
        backup = _backup_output_before_run(ox)
        if backup:
            print(f"[SpyDialer-People] Safety backup: {backup}")

    driver = build_driver(headless=headless)
    search_cache: dict[str, List[SpyDialerPeopleRow]] = {}
    working_df = kept_df.copy()
    saved = ox

    try:
        for n, row in enumerate(input_rows, start=1):
            owner_idx = int(row.get("input_row_index", 0))
            sts = _statuses_for_owner(existing_df, owner_idx)
            if "ok" in sts or _owner_has_phones_in_output(existing_df, owner_idx):
                print(
                    f"[SpyDialer-People] [{n}/{len(input_rows)}] SKIP "
                    f"(already ok in output): {row.get('owner_name_raw')}"
                )
                continue
            if sts & STATUS_RETRY:
                print(
                    f"[SpyDialer-People] [{n}/{len(input_rows)}] RETRY "
                    f"(was {', '.join(sorted(sts))}): {row.get('owner_name_raw')}"
                )
            k = _search_dedupe_key(row)
            if k not in search_cache:
                print(
                    f"[SpyDialer-People] [{n}/{len(input_rows)}] "
                    f"{row.get('owner_name_raw')} | {row.get('search_city')}, "
                    f"{row.get('search_state_abbr')}"
                )
                search_cache[k] = process_one_owner_with_network(
                    driver,
                    row,
                    pause_on_network_error=pause_on_network_error,
                )
                time.sleep(pause)
            else:
                print(
                    f"[SpyDialer-People] [{n}/{len(input_rows)}] "
                    f"Reuse cached search: {row.get('owner_name_raw')}"
                )

            clones: List[SpyDialerPeopleRow] = []
            for scraped in search_cache[k]:
                clone = SpyDialerPeopleRow(**asdict(scraped))
                clone.input_row_index = row.get("input_row_index", 0)
                clone.facility_number = row.get("facility_number")
                clone.facility_name = row.get("facility_name")
                clone.facility_street = row.get("facility_street")
                clone.facility_city = row.get("facility_city")
                clone.facility_state = row.get("facility_state")
                clone.owner_name_raw = row.get("owner_name_raw")
                clone.extra_source = dict(row.get("extra_source") or {})
                clones.append(clone)

            working_df = _merge_owner_results(
                working_df,
                owner_idx,
                clones,
                facility_number=row.get("facility_number"),
            )
            saved = _save_excel(working_df, ox)
            new_status = ", ".join(sorted({_normalize_status(c.status) for c in clones})) if clones else "?"
            print(
                f"[SpyDialer-People] Saved {len(working_df)} row(s) after "
                f"excel~{owner_idx} status={new_status!r} → {saved}"
            )
    finally:
        if quit_driver_when_done:
            try:
                driver.quit()
            except Exception:
                pass

    print(f"[SpyDialer-People] Done: {saved} ({len(working_df)} rows)")
    return working_df, saved


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Spy Dialer people search (parallel-safe with --offset, --limit, --output)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Two terminals on full ny.csv (~2974 individuals) — each gets its own output file automatically:

  # Terminal 1 → output/spy_dialer_ind_1_to_1487.xlsx
  python3 spy_dialer_people_automation.py --offset 0 --limit 1487

  # Terminal 2 → output/spy_dialer_ind_1488_to_2974.xlsx
  python3 spy_dialer_people_automation.py --offset 1487 --limit 1487

Bronx only (~237 individuals):

  python3 spy_dialer_people_automation.py --city BRONX --offset 0 --limit 118
  python3 spy_dialer_people_automation.py --city BRONX --offset 118 --limit 119

Verify partition without opening Chrome:

Re-run (same command + same --output): skips owners with done status; retries only
error, timeout, extract_error. Use --force to search every row again.

  python3 spy_dialer_people_automation.py --offset 0 --limit 1487 --dry-run
""",
    )
    p.add_argument("--input", type=str, default=None, help=f"Input csv/xlsx (default: {INPUT_CSV})")
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output xlsx (default: auto-named under output/ from --offset/--limit)",
    )
    p.add_argument(
        "--city",
        type=str,
        default=None,
        help="Only this Facility City from ny.csv (e.g. BRONX)",
    )
    p.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip first N individuals after corporate/unparseable filter (for parallel runs)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max individuals in this run (use with --offset for parallel workers)",
    )
    p.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Alias for --limit (legacy name)",
    )
    p.add_argument("--start-excel-row", type=int, default=None, help="Start at Excel row on sheet")
    p.add_argument(
        "--sheet",
        type=int,
        default=None,
        help="Excel sheet index (0-based). Rockland file uses 0 (Sheet1).",
    )
    p.add_argument("--dry-run", action="store_true", help="Print partition only; no browser")
    p.add_argument(
        "--force",
        action="store_true",
        help="Ignore existing output; re-search all rows in this partition",
    )
    p.add_argument(
        "--no-internet-wait",
        action="store_true",
        help="Do not pause/retry on network errors (not recommended)",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        default=None,
        help="Run Chrome headless (default: AUTOMATION_HEADLESS_CHROME env or true)",
    )
    p.add_argument(
        "--no-headless",
        action="store_true",
        help="Show Chrome window (overrides headless default)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.start_excel_row is not None:
        START_EXCEL_ROW = int(args.start_excel_row)

    lim = args.limit if args.limit is not None else args.max_rows
    in_path = args.input or INPUT_CSV
    city = args.city.strip().upper() if args.city else None
    if args.no_headless:
        run_headless = False
    elif args.headless:
        run_headless = True
    else:
        run_headless = None

    print("=" * 60)
    print("Spy Dialer — People search (NY export individuals)")
    print("Input:", in_path)
    if city:
        print("City filter:", city)
    if args.offset or lim is not None:
        print(f"Partition: offset={args.offset}, limit={lim}")
    print("Headless Chrome:", run_headless if run_headless is not None else HEADLESS)
    print("=" * 60)

    df, path = run_spy_dialer_people_batch(
        input_path=in_path,
        output_xlsx=args.output,
        individual_offset=args.offset,
        individual_limit=lim,
        filter_city=city,
        dry_run=args.dry_run,
        resume_from_output=RESUME_FROM_OUTPUT and not args.force,
        pause_on_network_error=PAUSE_ON_NETWORK_ERROR and not args.no_internet_wait,
        sheet_index=args.sheet,
        headless=run_headless,
    )
    if args.dry_run:
        print("Output would be:", path)
    if not args.dry_run:
        print("Saved:", path)
        if len(df):
            print(df.head(10))
