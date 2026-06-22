"""Load CSV/XLSX rows using user-selected column mapping."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from registry.services.parser import read_registry_file

from .names import split_full_name, to_search_parts


def peek_columns(file_path: str | Path) -> list[str]:
    df = read_registry_file(file_path)
    return list(df.columns)


def _cell(val: object) -> str:
    return str(val or '').strip()


def extract_first_phone(raw: str) -> str | None:
    """Pull one valid US 10-digit phone from a cell (handles 'a | b' output columns)."""
    text = str(raw or '').strip()
    if not text:
        return None
    for part in re.split(r'\||\s*/\s*|;', text):
        digits = re.sub(r'\D', '', part.strip())
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        if len(digits) == 10:
            return f'{digits[:3]}-{digits[3:6]}-{digits[6:]}'
    return None


def normalize_phone(raw: str) -> str:
    found = extract_first_phone(raw)
    return found or ''


def _state_abbrev(state: str) -> str:
    s = (state or '').strip().upper()
    if len(s) == 2:
        return s
    names = {
        'NEW YORK': 'NY', 'CALIFORNIA': 'CA', 'TEXAS': 'TX', 'FLORIDA': 'FL',
    }
    return names.get(s.upper(), s[:2] if s else '')


def load_mapped_rows(
    file_path: str | Path,
    *,
    search_mode: str,
    column_map: dict[str, str],
) -> list[dict[str, Any]]:
    df = read_registry_file(file_path)
    rows: list[dict[str, Any]] = []

    for i, ser in df.iterrows():
        excel_row = int(i) + 2
        extra = {str(c): _cell(ser.get(c)) for c in df.columns}

        if search_mode == 'phone':
            phone_col = column_map.get('phone')
            if not phone_col or phone_col not in df.columns:
                raise ValueError('Phone column mapping is required.')
            phone = normalize_phone(_cell(ser.get(phone_col)))
            if not phone:
                continue
            rows.append({
                'input_row_index': excel_row,
                'search_phone': phone,
                'search_label': phone,
                'owner_name_raw': phone,
                'extra_source': extra,
            })
            continue

        name_mode = column_map.get('name_mode', 'split_columns')
        state_col = column_map.get('state')
        if not state_col or state_col not in df.columns:
            raise ValueError('Missing column mapping for "state".')

        city_col = column_map.get('city') or column_map.get('address') or ''
        city = _cell(ser.get(city_col)) if city_col and city_col in df.columns else ''
        state = _cell(ser.get(state_col))

        if name_mode == 'full_name':
            full_col = column_map.get('full_name')
            if not full_col or full_col not in df.columns:
                raise ValueError('Missing column mapping for "full_name".')
            raw_name = _cell(ser.get(full_col))
            if not raw_name:
                continue
            first, last, middle = split_full_name(raw_name)
            if not first or not last:
                continue
            parts = to_search_parts(first, last, middle)
            owner_raw = raw_name
        else:
            for key in ('first_name', 'last_name'):
                col = column_map.get(key)
                if not col or col not in df.columns:
                    raise ValueError(f'Missing column mapping for "{key}".')
            first = _cell(ser.get(column_map['first_name']))
            last = _cell(ser.get(column_map['last_name']))
            if not first and not last:
                continue
            middle_col = column_map.get('middle_name') or ''
            middle = _cell(ser.get(middle_col)) if middle_col else ''
            parts = to_search_parts(first, last, middle)
            owner_raw = ' '.join(p for p in [first, middle, last] if p).strip()

        rows.append({
            'input_row_index': excel_row,
            **parts,
            'search_city': city,
            'search_state': state,
            'search_state_abbr': _state_abbrev(state),
            'search_label': owner_raw,
            'owner_name_raw': owner_raw,
            'extra_source': extra,
        })

    return rows
