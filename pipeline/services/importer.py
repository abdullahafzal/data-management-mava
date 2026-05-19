import csv
import io
from pathlib import Path

import pandas as pd


SUPPORTED_EXTENSIONS = {'.csv', '.xlsx', '.xls'}
PREVIEW_CELL_MAX_LEN = 120
# Cap only for extremely large files (browser performance). 0 = no cap (show all).
PREVIEW_ROW_CAP = 0


def _read_dataframe(file_path: str | Path) -> pd.DataFrame:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == '.csv':
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if suffix in ('.xlsx', '.xls'):
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    raise ValueError(f'Unsupported file type: {suffix}')


def _truncate_cell(value, max_len: int = PREVIEW_CELL_MAX_LEN) -> str:
    text = '' if value is None else str(value)
    if len(text) > max_len:
        return text[: max_len - 1] + '…'
    return text


def preview_upload(
    file_path: str | Path,
    *,
    max_rows: int | None = None,
) -> dict:
    """
    Outscraper export for on-page preview (all rows by default).
    Returns headers, row values (strings), and truncation flags.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f'Unsupported file type: {suffix}')

    df = _read_dataframe(path).fillna('')
    total_rows = len(df)
    total_columns = len(df.columns)

    cap = max_rows if max_rows is not None else PREVIEW_ROW_CAP
    if cap and cap > 0:
        sample = df.head(cap)
        truncated_rows = total_rows > cap
    else:
        sample = df
        truncated_rows = False

    headers = [str(c) for c in sample.columns.tolist()]
    rows = [
        [_truncate_cell(cell) for cell in row]
        for row in sample.values.tolist()
    ]

    return {
        'headers': headers,
        'rows': rows,
        'preview_rows': len(sample),
        'total_rows': total_rows,
        'total_columns': total_columns,
        'truncated_rows': truncated_rows,
    }


def parse_upload(file_path: str | Path) -> dict:
    """
    Parse an Outscraper CSV/XLSX export.
    Returns column names, row count, and a normalized dataframe.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f'Unsupported file type "{suffix}". Use CSV or Excel (.xlsx).'
        )

    df = _read_dataframe(path)
    df = df.fillna('')
    columns = [str(c) for c in df.columns.tolist()]
    return {
        'dataframe': df,
        'columns': columns,
        'row_count': len(df),
        'file_format': suffix.lstrip('.'),
    }


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, quoting=csv.QUOTE_MINIMAL)
    return buffer.getvalue().encode('utf-8')
