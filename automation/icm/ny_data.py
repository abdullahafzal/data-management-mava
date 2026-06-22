"""
Shared NY State list loader and master-sheet helpers for the enrichment pipeline.

Input: file/ny.csv
Master: output/master_ny.xlsx (built by ny_step0_init_master.py)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_RUN_INPUT: Path | None = None
_RUN_MASTER: Path | None = None

INPUT_CSV = _PROJECT_ROOT / "file" / "ny.csv"
MASTER_XLSX = _PROJECT_ROOT / "output" / "master_ny.xlsx"


def configure_run_paths(
    *,
    input_path: Path | str | None = None,
    master_path: Path | str,
) -> None:
    """Point NY helpers at per-job input/master files (Django automation runs)."""
    global _RUN_INPUT, _RUN_MASTER, INPUT_CSV, MASTER_XLSX
    _RUN_INPUT = Path(input_path) if input_path else None
    _RUN_MASTER = Path(master_path)
    if _RUN_INPUT is not None:
        INPUT_CSV = _RUN_INPUT
    MASTER_XLSX = _RUN_MASTER

# --- Which rows from ny.csv go into the master (Step 0) ---
# None = all NY (~3471). Set "BRONX" for ~252 rows only.
FILTER_FACILITY_CITY: Optional[str] = None
# Statewide chunks (use with FILTER_FACILITY_CITY = None):
#   chunk 1: OFFSET=0,   SIZE=250 → rows 1–250 of ny.csv
#   chunk 2: OFFSET=250, SIZE=250 → rows 251–500
#   chunk 3: OFFSET=500, SIZE=250 → etc.
NY_CHUNK_OFFSET: int = 0
NY_CHUNK_SIZE: Optional[int] = None  # e.g. 250; None = from offset to end of filtered list
MAX_ROWS: Optional[int] = None  # legacy alias; prefer NY_CHUNK_SIZE

# How many NEW rows to process per run for Google phone / website email (step 1 & 2).
# None = all rows still missing that field. Re-run to process the next batch.
BATCH_SIZE_PER_RUN: Optional[int] = 50

# Columns added to master (enrichment pipeline)
ENRICHMENT_COLUMNS = (
    "pipeline_row_id",
    "owner_type",
    "office_phone",
    "office_phone_source",
    "google_status",
    "google_error",
    "google_search_query",
    "work_email",
    "work_email_source",
    "email_status",
    "email_error",
    "personal_cell",
    "personal_email",
    "icm_status",
    "icm_verified",          # correct | partial | incorrect | no_results | error | skipped_corporate
    "icm_name_match",        # yes | no
    "icm_location_match",    # yes | no
    "icm_report_name",       # name on ICM report (compare to Owner Name)
    "icm_locations_found",   # locations from ICM (short, for verification display)
    "icm_phone_numbers",     # all phones scraped from ICM (pipe-separated)
    "icm_emails",            # all emails scraped from ICM (pipe-separated)
    "icm_locations_full",    # full locations / addresses from ICM reports
    "icm_page_url",          # ICM report page URL
    "icm_result_card_name",  # name shown on ICM search result card
    "icm_card_locations",    # locations preview on ICM search result card
    "icm_records_found",     # count of ICM result records scraped for this owner
    "icm_scrape_error",      # scrape/search errors (if any)
    "pipeline_notes",
)

_CORPORATE_RE = re.compile(
    r"\b(corp|corporation|inc|incorporated|llc|l\.l\.c\.|ltd|limited|co\.|company)\b",
    re.IGNORECASE,
)


def _norm_col_map(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().lower(): c for c in df.columns}


def _cell_str(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def classify_owner(owner: str, owner_overflow: str = "") -> str:
    blob = " ".join(_cell_str(x) for x in (owner, owner_overflow) if _cell_str(x))
    if _CORPORATE_RE.search(blob):
        return "corporate"
    parts = re.sub(r"\s+", " ", (owner or "").strip()).split()
    if len(parts) >= 2:
        return "individual"
    if len(parts) == 1 and parts[0]:
        return "individual"
    return "unparseable"


def facility_display_name(row: pd.Series, cmap: dict[str, str]) -> str:
    def g(*names: str) -> str:
        for n in names:
            k = n.strip().lower()
            if k in cmap:
                v = _cell_str(row.get(cmap[k]))
                if v:
                    return v
        return ""

    name = g("facility name", "facility name ")
    overflow = g("facility name overflow")
    if overflow and overflow.upper() not in name.upper():
        return f"{name} {overflow}".strip()
    return name


def active_master_path(row_count: Optional[int] = None) -> Path:
    """
    Where Step 0–2 read/write the master workbook.
    Chunked statewide runs use separate files so Bronx master is not overwritten.
    """
    if _RUN_MASTER is not None:
        return _RUN_MASTER
    off = int(NY_CHUNK_OFFSET or 0)
    size = NY_CHUNK_SIZE if NY_CHUNK_SIZE is not None else MAX_ROWS
    if size is not None and not FILTER_FACILITY_CITY:
        n = int(row_count) if row_count is not None else int(size)
        end = off + n
        return _PROJECT_ROOT / "output" / f"master_ny_{off + 1}_to_{end}.xlsx"
    if off > 0 and not FILTER_FACILITY_CITY:
        end = off + (int(row_count) if row_count is not None else 0)
        return _PROJECT_ROOT / "output" / f"master_ny_{off + 1}_to_{end}.xlsx"
    return MASTER_XLSX


def load_ny_dataframe(
    csv_path: Path | str | None = None,
    filter_city: Optional[str] = None,
    max_rows: Optional[int] = None,
    chunk_offset: Optional[int] = None,
    chunk_size: Optional[int] = None,
) -> pd.DataFrame:
    path = Path(csv_path) if csv_path else INPUT_CSV
    if not path.is_file():
        raise FileNotFoundError(path)

    fc = FILTER_FACILITY_CITY if filter_city is None else filter_city
    off = NY_CHUNK_OFFSET if chunk_offset is None else int(chunk_offset)
    size = NY_CHUNK_SIZE if chunk_size is None else chunk_size
    if size is None and max_rows is None:
        size = MAX_ROWS

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df.rename(columns=lambda c: str(c).strip())

    cmap = _norm_col_map(df)
    city_col = cmap.get("facility city")
    if fc and city_col:
        want = fc.strip().upper()
        df = df[df[city_col].astype(str).str.strip().str.upper() == want].copy()

    if off > 0:
        df = df.iloc[off:].copy()
    if size is not None:
        df = df.head(int(size)).copy()

    df.reset_index(drop=True, inplace=True)
    return df


def build_master_from_ny(df: pd.DataFrame) -> pd.DataFrame:
    """Add enrichment columns; preserve all NY State columns."""
    out = df.copy()
    cmap = _norm_col_map(out)

    owner_col = cmap.get("owner name") or cmap.get("owner name ")
    ov_col = cmap.get("owner name overflow")

    out["pipeline_row_id"] = [i + 2 for i in range(len(out))]  # excel-style row #

    if owner_col:
        out["owner_type"] = [
            classify_owner(
                _cell_str(out.iloc[i].get(owner_col)),
                _cell_str(out.iloc[i].get(ov_col)) if ov_col else "",
            )
            for i in range(len(out))
        ]
    else:
        out["owner_type"] = ""

    for col in ENRICHMENT_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    # Reorder: source cols first, then enrichment (stable order)
    enrich = [c for c in ENRICHMENT_COLUMNS if c in out.columns]
    base = [c for c in out.columns if c not in enrich]
    return out[base + enrich]


def save_master(df: pd.DataFrame, path: Path | str | None = None) -> str:
    dest = Path(path) if path else active_master_path(len(df))
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(dest, index=False, engine="openpyxl")
    return str(dest)


def load_master(path: Path | str | None = None) -> pd.DataFrame:
    dest = Path(path) if path else active_master_path()
    if not dest.is_file():
        raise FileNotFoundError(
            f"Master file not found: {dest}. Run: python3 ny_step0_init_master.py"
        )
    return pd.read_excel(dest, dtype=str, keep_default_na=False)


_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)
_JUNK_EMAIL_DOMAIN = re.compile(
    r"@(?:example\.com|sentry\.io|wixpress\.com|domain\.com|email\.com|yelp\.com|google\.com)",
    re.I,
)


def normalize_name_key(name: str) -> str:
    s = (name or "").upper()
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(
        r"\b(CORP|CORPORATION|INC|INCORPORATED|LLC|LTD|LIMITED|CO|COMPANY)\b",
        " ",
        s,
    )
    return re.sub(r"\s+", " ", s).strip()


def normalize_facility_number(v: object) -> str:
    s = re.sub(r"\D", "", _cell_str(v))
    return s.lstrip("0") or s


def is_plausible_work_email(email: str) -> bool:
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return False
    if _JUNK_EMAIL_DOMAIN.search(e):
        return False
    if e.startswith("noreply") or e.startswith("no-reply"):
        return False
    return True


def pick_first_email(*candidates: object) -> str:
    for c in candidates:
        s = _cell_str(c)
        if not s:
            continue
        for m in _EMAIL_RE.finditer(s):
            if is_plausible_work_email(m.group(0)):
                return m.group(0).lower()
    return ""


def ensure_enrichment_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ENRICHMENT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out


def rows_needing_icm(df: pd.DataFrame) -> pd.DataFrame:
    """Individual owners not yet checked on ICM (icm_verified empty)."""
    df = ensure_enrichment_columns(df)
    ind = df["owner_type"].astype(str).str.strip() == "individual"
    not_done = df["icm_verified"].astype(str).str.strip() == ""
    return df.loc[ind & not_done].copy()


def take_batch(todo: pd.DataFrame, batch_cap: Optional[int]) -> pd.DataFrame:
    """First N rows from todo (file order). None = all."""
    if batch_cap is None:
        return todo.copy()
    return todo.head(int(batch_cap)).copy()


def batch_pipeline_row_ids(df: pd.DataFrame, indices: pd.Index) -> list[str]:
    if "pipeline_row_id" not in df.columns:
        return [str(int(i) + 2) for i in indices]
    return [str(df.at[i, "pipeline_row_id"]) for i in indices]


def rows_needing_work_email(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_enrichment_columns(df)
    empty = df["work_email"].astype(str).str.strip() == ""
    return df.loc[empty].copy()


def rows_needing_google(df: pd.DataFrame) -> pd.DataFrame:
    """Rows without office_phone yet (any owner type — corp still gets shop phone)."""
    if "office_phone" not in df.columns:
        return df.copy()
    empty = df["office_phone"].astype(str).str.strip() == ""
    return df.loc[empty].copy()


def google_search_query(row: pd.Series, cmap: dict[str, str]) -> str:
    name = facility_display_name(row, cmap)
    city_col = cmap.get("facility city")
    state_col = cmap.get("facility state")
    city = _cell_str(row.get(city_col)) if city_col else ""
    state = _cell_str(row.get(state_col)) if state_col else "NY"
    parts = [name]
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    return " ".join(p for p in parts if p).strip()
