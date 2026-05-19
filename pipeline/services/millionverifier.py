import re
from pathlib import Path

import pandas as pd

from .importer import _read_dataframe, dataframe_to_csv_bytes

# MillionVerifier / common verifier export column names (lowercase)
STATUS_COLUMN_CANDIDATES = [
    'result',
    'quality',
    'status',
    'email status',
    'email_status',
    'verification status',
    'verification_status',
    'mv_result',
]

# Map raw status values → export category folder names
CATEGORY_ALIASES = {
    'ok': 'good',
    'good': 'good',
    'valid': 'good',
    'deliverable': 'good',
    'risky': 'risky',
    'catch_all': 'risky',
    'catch-all': 'risky',
    'catchall': 'risky',
    'unknown': 'unknown',
    'accept_all': 'unknown',
    'accept-all': 'unknown',
    'invalid': 'invalid',
    'bad': 'invalid',
    'undeliverable': 'invalid',
    'disposable': 'disposable',
    'spamtrap': 'invalid',
    'role': 'risky',
}


def detect_status_column(columns: list[str]) -> str | None:
    lowered = {c.lower().strip(): c for c in columns}
    for candidate in STATUS_COLUMN_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    for col in columns:
        if 'result' in col.lower() or 'quality' in col.lower():
            return col
    return None


def normalize_category(raw_value: str) -> str:
    if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
        return 'unknown'
    key = str(raw_value).strip().lower()
    key = re.sub(r'[\s\-]+', '_', key)
    return CATEGORY_ALIASES.get(key, key or 'unknown')


def split_verification_results(
    source_path: str | Path,
    status_column: str | None = None,
) -> tuple[str, dict[str, tuple[bytes, int]]]:
    """
    Split a MillionVerifier result file into category CSVs.
    Returns (status_column_used, {category: (csv_bytes, row_count)}).
    """
    df = _read_dataframe(source_path)
    df = df.fillna('')

    columns = [str(c) for c in df.columns.tolist()]
    col = status_column or detect_status_column(columns)
    if not col:
        raise ValueError(
            'Could not detect verification status column. '
            'Expected a column like "result", "quality", or "status".'
        )
    if col not in df.columns:
        raise ValueError(f'Status column "{col}" not found in file.')

    df['_mv_category'] = df[col].apply(normalize_category)

    exports: dict[str, tuple[bytes, int]] = {}
    for category, group in df.groupby('_mv_category', sort=True):
        out = group.drop(columns=['_mv_category'])
        exports[category] = (dataframe_to_csv_bytes(out), len(out))

    return col, exports
