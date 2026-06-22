"""Deterministic weekly diff between NY registry snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .columns import is_closed_status
from .parser import read_registry_file

PREVIEW_SAMPLE_LIMIT = 5
CHANGE_FIELD_SAMPLE_LIMIT = 8


def _normalize_key(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower()


def _row_fingerprint(row: pd.Series, *, exclude: str) -> str:
    payload = {str(k): str(row[k]) for k in row.index if str(k) != exclude}
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _row_dict(row: pd.Series) -> dict[str, str]:
    return {str(k): str(row[k]) for k in row.index}


def _status_breakdown(df: pd.DataFrame, status_col: str | None) -> dict[str, Any]:
    if not status_col or status_col not in df.columns:
        return {
            'status_column': status_col,
            'active_count': None,
            'closed_count': None,
            'unknown_count': len(df),
            'by_status': {},
        }
    counts: dict[str, int] = {}
    active = 0
    closed = 0
    for val in df[status_col].astype(str):
        label = val.strip() or '(blank)'
        counts[label] = counts.get(label, 0) + 1
        if is_closed_status(val):
            closed += 1
        else:
            active += 1
    top = dict(sorted(counts.items(), key=lambda x: -x[1])[:15])
    return {
        'status_column': status_col,
        'active_count': active,
        'closed_count': closed,
        'unknown_count': 0,
        'by_status': top,
    }


def compare_registry_files(
    *,
    new_path: str | Path,
    baseline_path: str | Path | None,
    key_column: str,
    status_column: str | None = None,
) -> dict[str, Any]:
    """
    Compare new weekly export to the last approved baseline.
    Returns stats + small samples (full lists available via re-diff for download).
    """
    new_df = read_registry_file(new_path)
    if key_column not in new_df.columns:
        raise ValueError(f'Key column "{key_column}" not found in new file.')

    new_df = new_df.copy()
    new_df['_registry_key'] = _normalize_key(new_df[key_column])
    dup_mask = new_df['_registry_key'].duplicated(keep=False) & (new_df['_registry_key'] != '')
    duplicate_key_count = int(dup_mask.sum())

    result: dict[str, Any] = {
        'key_column': key_column,
        'status_column': status_column,
        'new_file_rows': len(new_df),
        'baseline_file_rows': 0,
        'duplicate_keys_in_new': duplicate_key_count,
        'is_initial_baseline': baseline_path is None,
        'new_status': _status_breakdown(new_df, status_column),
    }

    if baseline_path is None:
        result.update({
            'new_count': len(new_df),
            'updated_count': 0,
            'removed_count': 0,
            'unchanged_count': 0,
            'changed_fields': [],
            'samples': {
                'new': [_row_dict(r) for _, r in new_df.head(PREVIEW_SAMPLE_LIMIT).iterrows()],
                'updated': [],
                'removed': [],
            },
        })
        return result

    base_df = read_registry_file(baseline_path)
    if key_column not in base_df.columns:
        raise ValueError(f'Key column "{key_column}" not found in baseline file.')

    base_df = base_df.copy()
    base_df['_registry_key'] = _normalize_key(base_df[key_column])
    result['baseline_file_rows'] = len(base_df)
    result['baseline_status'] = _status_breakdown(base_df, status_column)

    new_keys = set(new_df['_registry_key']) - {''}
    base_keys = set(base_df['_registry_key']) - {''}

    added_keys = sorted(new_keys - base_keys)
    removed_keys = sorted(base_keys - new_keys)
    common_keys = new_keys & base_keys

    new_by_key = new_df.set_index('_registry_key', drop=False)
    base_by_key = base_df.set_index('_registry_key', drop=False)

    updated_keys: list[str] = []
    unchanged_count = 0
    field_change_counts: dict[str, int] = {}

    for key in common_keys:
        new_row = new_by_key.loc[key]
        base_row = base_by_key.loc[key]
        if isinstance(new_row, pd.DataFrame):
            new_row = new_row.iloc[-1]
        if isinstance(base_row, pd.DataFrame):
            base_row = base_row.iloc[-1]
        if _row_fingerprint(new_row, exclude='_registry_key') == _row_fingerprint(
            base_row, exclude='_registry_key'
        ):
            unchanged_count += 1
            continue
        updated_keys.append(key)
        for col in new_df.columns:
            if col == '_registry_key':
                continue
            if str(new_row[col]) != str(base_row[col]):
                field_change_counts[col] = field_change_counts.get(col, 0) + 1

    def _sample_rows(keys: list[str], *, kind: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in keys[:PREVIEW_SAMPLE_LIMIT]:
            row = new_by_key.loc[key] if kind != 'removed' else base_by_key.loc[key]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            entry = {'registry_key': key, 'row': _row_dict(row)}
            if kind == 'updated':
                brow = base_by_key.loc[key]
                if isinstance(brow, pd.DataFrame):
                    brow = brow.iloc[-1]
                changed = []
                for col in new_df.columns:
                    if col == '_registry_key':
                        continue
                    if str(row[col]) != str(brow[col]):
                        changed.append({
                            'field': col,
                            'old': str(brow[col]),
                            'new': str(row[col]),
                        })
                entry['changes'] = changed[:CHANGE_FIELD_SAMPLE_LIMIT]
            rows.append(entry)
        return rows

    top_changed_fields = sorted(
        field_change_counts.items(),
        key=lambda x: -x[1],
    )[:CHANGE_FIELD_SAMPLE_LIMIT]

    result.update({
        'new_count': len(added_keys),
        'updated_count': len(updated_keys),
        'removed_count': len(removed_keys),
        'unchanged_count': unchanged_count,
        'changed_fields': [{'field': f, 'count': c} for f, c in top_changed_fields],
        'samples': {
            'new': _sample_rows(added_keys, kind='new'),
            'updated': _sample_rows(updated_keys, kind='updated'),
            'removed': _sample_rows(removed_keys, kind='removed'),
        },
        'key_lists': {
            'new': added_keys,
            'updated': updated_keys,
            'removed': removed_keys,
        },
    })
    return result
