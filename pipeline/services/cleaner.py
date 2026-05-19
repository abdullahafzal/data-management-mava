import io
from pathlib import Path

import pandas as pd

from .importer import _read_dataframe, dataframe_to_csv_bytes


def build_cleaned_csv(
    source_path: str | Path,
    selected_columns: list[str],
) -> tuple[bytes, int]:
    """
    Keep only user-selected columns from the original Outscraper file.
    Drops rows where all selected values are empty.
    """
    if not selected_columns:
        raise ValueError('Select at least one column to keep.')

    df = _read_dataframe(source_path)
    df = df.fillna('')

    missing = [c for c in selected_columns if c not in df.columns]
    if missing:
        raise ValueError(f'Columns not found in file: {", ".join(missing)}')

    cleaned = df[selected_columns].copy()
    mask = cleaned.apply(
        lambda row: any(str(v).strip() for v in row), axis=1
    )
    cleaned = cleaned[mask]

    return dataframe_to_csv_bytes(cleaned), len(cleaned)
