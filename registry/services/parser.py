"""Load NY registry CSV/XLSX exports."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

SUPPORTED_EXTENSIONS = {'.csv', '.xlsx', '.xls'}


def read_registry_file(file_path: str | Path) -> pd.DataFrame:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f'Unsupported file type "{suffix}". Use CSV or Excel (.xlsx).')
    if suffix == '.csv':
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    else:
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    df = df.fillna('')
    df.columns = [str(c).strip() for c in df.columns]
    return df


def file_format_label(file_path: str | Path) -> str:
    return Path(file_path).suffix.lower().lstrip('.')
