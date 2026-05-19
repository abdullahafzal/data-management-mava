"""
Build Diana handoff CSV: rows that still need manual contact enrichment.

Per transcript, Diana receives leads missing usable email or phone. This export
uses the **full Outscraper** row and adds a `diana_reason` column (`missing_email`,
`missing_phone`, or both).
"""

from pathlib import Path

import pandas as pd

from .importer import dataframe_to_csv_bytes

EMAIL_COLUMNS = ('email_1', 'email_2', 'email_3')
PHONE_COLUMNS = ('phone', 'phone_1', 'phone_2', 'phone_3')


def _nonempty(val) -> bool:
    if val is None:
        return False
    if isinstance(val, float) and pd.isna(val):
        return False
    return bool(str(val).strip())


def row_email_missing(row: pd.Series, file_columns: list[str]) -> bool:
    found = [_nonempty(row[c]) for c in EMAIL_COLUMNS if c in file_columns]
    return not found or not any(found)


def row_phone_missing(row: pd.Series, file_columns: list[str]) -> bool:
    found = [_nonempty(row[c]) for c in PHONE_COLUMNS if c in file_columns]
    return not found or not any(found)


def build_diana_handoff_csv(source_path: str | Path) -> tuple[bytes, int]:
    """
    Return (csv_bytes, row_count): rows needing email and/or phone enrichment.
    """
    path = Path(source_path)
    suffix = path.suffix.lower()
    if suffix == '.csv':
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    else:
        df = pd.read_excel(path, dtype=str, keep_default_na=False)

    df = df.fillna('')
    file_columns = list(df.columns)

    reason_rows = []
    for idx, row in df.iterrows():
        miss_e = row_email_missing(row, file_columns)
        miss_p = row_phone_missing(row, file_columns)
        if not miss_e and not miss_p:
            continue
        parts = []
        if miss_e:
            parts.append('missing_email')
        if miss_p:
            parts.append('missing_phone')
        reason_rows.append((idx, ', '.join(parts)))

    if not reason_rows:
        out = pd.DataFrame(columns=['diana_reason', *df.columns])
    else:
        idxs = [x[0] for x in reason_rows]
        out = df.loc[idxs].copy()
        reasons = [x[1] for x in reason_rows]
        out.insert(0, 'diana_reason', reasons)

    return dataframe_to_csv_bytes(out), len(out)
