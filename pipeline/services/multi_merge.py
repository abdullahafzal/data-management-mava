"""
Multi-file union merge for the pipeline.

Rules (product):
- Union all rows across files (200 + 300 with 0 matches → 500).
- Match preference: Facility # → Street+City(+Zip) → Business name+City.
- Never discard data: on conflicts keep base value and add source-tagged columns.
- First uploaded file is the match base for key preference; unmatched rows from
  every file are kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .importer import _read_dataframe, dataframe_to_csv_bytes

FACILITY_ALIASES = (
    'facility #', 'facility#', 'facility number', 'facility_number',
    'facility id', 'facility_id', 'license', 'license number', 'license #',
    'dmv number', 'dmv_number', 'id',
)
STREET_ALIASES = (
    'facility street', 'street', 'street address', 'address', 'full_address',
    'full address', 'site address',
)
CITY_ALIASES = (
    'facility city', 'city', 'city name',
)
ZIP_ALIASES = (
    'zip', 'zip code', 'zipcode', 'postal', 'facility zip', 'facility zip code',
)
NAME_ALIASES = (
    'facility name', 'business name', 'company', 'company_name', 'name',
    'legal name', 'trade name', 'store name',
)


def _norm_header(h: object) -> str:
    return re.sub(r'\s+', ' ', str(h or '').strip().lower())


def _source_tag(label: str) -> str:
    stem = Path(label).stem if label else 'source'
    tag = re.sub(r'[^a-zA-Z0-9]+', '_', stem).strip('_').lower()
    return (tag or 'source')[:40]


def _cell(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    return str(v).strip()


def _norm_token(v: object) -> str:
    s = _cell(v).upper()
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _find_col(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    cmap = {_norm_header(c): c for c in columns}
    for alias in aliases:
        if alias in cmap:
            return cmap[alias]
    return None


def _prepare_frame(df: pd.DataFrame, source_label: str) -> pd.DataFrame:
    out = df.copy()
    out = out.rename(columns=lambda c: str(c).strip())
    out = out.fillna('')
    for col in out.columns:
        out[col] = out[col].map(_cell)
    out['_merge_source'] = source_label
    out['_merge_row'] = range(len(out))
    return out


def _match_key(row: pd.Series, columns: list[str], level: str) -> str | None:
    fac = _find_col(columns, FACILITY_ALIASES)
    street = _find_col(columns, STREET_ALIASES)
    city = _find_col(columns, CITY_ALIASES)
    zip_c = _find_col(columns, ZIP_ALIASES)
    name = _find_col(columns, NAME_ALIASES)

    if level == 'facility':
        if not fac:
            return None
        val = _norm_token(row.get(fac))
        return f'fac::{val}' if val else None

    if level == 'address':
        if not street or not city:
            return None
        s = _norm_token(row.get(street))
        c = _norm_token(row.get(city))
        if not s or not c:
            return None
        z = _norm_token(row.get(zip_c)) if zip_c else ''
        return f'addr::{s}|{c}|{z}' if z else f'addr::{s}|{c}'

    if level == 'name_city':
        if not name or not city:
            return None
        n = _norm_token(row.get(name))
        c = _norm_token(row.get(city))
        return f'name::{n}|{c}' if n and c else None

    return None


def _build_index(df: pd.DataFrame) -> dict[str, dict[str, list[int]]]:
    cols = list(df.columns)
    indexes: dict[str, dict[str, list[int]]] = {
        'facility': {},
        'address': {},
        'name_city': {},
    }
    for i, row in df.iterrows():
        for level in ('facility', 'address', 'name_city'):
            key = _match_key(row, cols, level)
            if key:
                indexes[level].setdefault(key, []).append(int(i))
    return indexes


def _find_match(
    row: pd.Series,
    columns: list[str],
    indexes: dict[str, dict[str, list[int]]],
    used: set[int],
) -> tuple[int | None, str]:
    for level in ('facility', 'address', 'name_city'):
        key = _match_key(row, columns, level)
        if not key:
            continue
        for idx in indexes.get(level, {}).get(key, []):
            if idx not in used:
                return idx, level
    return None, ''


def _unique_col(existing: set[str], desired: str) -> str:
    if desired not in existing:
        return desired
    n = 2
    while f'{desired}__{n}' in existing:
        n += 1
    return f'{desired}__{n}'


@dataclass
class MergeResult:
    dataframe: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)
    csv_bytes: bytes = b''


def merge_frames(
    labeled_frames: list[tuple[str, pd.DataFrame]],
) -> MergeResult:
    """
    labeled_frames: list of (source_label, dataframe) in upload order.
    First frame is the preferred match base for identity keys.
    """
    if not labeled_frames:
        raise ValueError('At least one file is required to merge.')

    prepared: list[tuple[str, pd.DataFrame]] = [
        (label, _prepare_frame(df, label)) for label, df in labeled_frames
    ]

    file_stats = [
        {'label': label, 'rows': len(df), 'columns': list(df.columns)}
        for label, df in prepared
    ]

    base_label, merged = prepared[0]
    matched_pairs: list[dict[str, Any]] = []
    only_secondary: list[dict[str, Any]] = []
    match_levels = {'facility': 0, 'address': 0, 'name_city': 0}

    for sec_label, sec_df in prepared[1:]:
        indexes = _build_index(merged)
        used: set[int] = set()
        sec_cols = [c for c in sec_df.columns if not c.startswith('_merge_')]
        tag = _source_tag(sec_label)

        col_map: dict[str, str] = {}
        existing = set(str(c) for c in merged.columns)
        for col in sec_cols:
            if col not in existing:
                col_map[col] = col
                merged[col] = ''
                existing.add(col)
            else:
                sibling = _unique_col(existing, f'{col}__{tag}')
                col_map[col] = sibling
                if sibling not in existing:
                    merged[sibling] = ''
                    existing.add(sibling)

        if 'row_sources' not in merged.columns:
            merged['row_sources'] = base_label
        if 'match_key_used' not in merged.columns:
            merged['match_key_used'] = ''

        unmatched_rows: list[dict[str, Any]] = []

        for _, srow in sec_df.iterrows():
            match_idx, level = _find_match(srow, list(sec_df.columns), indexes, used)
            if match_idx is None:
                new_row: dict[str, Any] = {c: '' for c in merged.columns}
                for col in sec_cols:
                    if col in merged.columns:
                        new_row[col] = _cell(srow.get(col))
                    else:
                        new_row[col_map[col]] = _cell(srow.get(col))
                new_row['row_sources'] = sec_label
                new_row['match_key_used'] = 'unmatched_new'
                unmatched_rows.append(new_row)
                only_secondary.append({'source': sec_label})
                continue

            used.add(match_idx)
            match_levels[level] = match_levels.get(level, 0) + 1
            matched_pairs.append({
                'source': sec_label,
                'base_index': match_idx,
                'level': level,
            })

            sources = _cell(merged.at[match_idx, 'row_sources'])
            if sec_label not in sources.split(' | '):
                merged.at[match_idx, 'row_sources'] = (
                    f'{sources} | {sec_label}' if sources else sec_label
                )
            prev_level = _cell(merged.at[match_idx, 'match_key_used'])
            if not prev_level or prev_level == 'unmatched_new':
                merged.at[match_idx, 'match_key_used'] = level

            for col in sec_cols:
                sec_val = _cell(srow.get(col))
                if not sec_val:
                    continue
                base_val = _cell(merged.at[match_idx, col]) if col in merged.columns else ''
                sibling = col_map[col]

                if not base_val:
                    if col in merged.columns:
                        merged.at[match_idx, col] = sec_val
                    if sibling != col:
                        merged.at[match_idx, sibling] = sec_val
                elif base_val == sec_val:
                    if sibling != col and not _cell(merged.at[match_idx, sibling]):
                        merged.at[match_idx, sibling] = sec_val
                else:
                    if sibling == col:
                        sibling = _unique_col(set(merged.columns), f'{col}__{tag}')
                        merged[sibling] = ''
                    merged.at[match_idx, sibling] = sec_val

        if unmatched_rows:
            merged = pd.concat(
                [merged, pd.DataFrame(unmatched_rows)],
                ignore_index=True,
            )

    drop_helpers = [c for c in merged.columns if c in ('_merge_source', '_merge_row')]
    merged = merged.drop(columns=drop_helpers, errors='ignore')

    base_rows = file_stats[0]['rows'] if file_stats else 0
    secondary_rows = sum(s['rows'] for s in file_stats[1:])
    matched_count = len(matched_pairs)
    union_rows = len(merged)

    report = {
        'files': [
            {'label': s['label'], 'rows': s['rows'], 'columns': len(s['columns'])}
            for s in file_stats
        ],
        'matched_pairs': matched_count,
        'match_by_level': match_levels,
        'only_in_secondary_approx': len(only_secondary),
        'base_rows': base_rows,
        'secondary_rows': secondary_rows,
        'union_rows': union_rows,
        'formula': (
            f'{base_rows} + {secondary_rows} - {matched_count} matches ≈ {union_rows} rows'
        ),
    }

    csv_bytes = dataframe_to_csv_bytes(merged)
    return MergeResult(dataframe=merged, report=report, csv_bytes=csv_bytes)


def merge_files(paths: list[tuple[str, str | Path]]) -> MergeResult:
    """paths: list of (label, filesystem path)."""
    frames: list[tuple[str, pd.DataFrame]] = []
    for label, path in paths:
        df = _read_dataframe(path)
        if df.empty:
            continue
        frames.append((label, df))
    if not frames:
        raise ValueError('No readable rows found in uploaded files.')
    return merge_frames(frames)
