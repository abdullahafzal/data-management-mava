import csv
import io
from pathlib import Path

import pandas as pd


SUPPORTED_EXTENSIONS = {'.csv', '.xlsx', '.xls'}


def _read_dataframe(file_path: str | Path) -> pd.DataFrame:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == '.csv':
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if suffix in ('.xlsx', '.xls'):
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    raise ValueError(f'Unsupported file type: {suffix}')


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
