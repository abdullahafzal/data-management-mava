"""Build CSV exports from XVerify phone verification responses."""

from __future__ import annotations

import csv
import io
from typing import Any

GOOD_STATUSES = frozenset({'valid', 'ok', 'good'})


def is_valid_status(status: str) -> bool:
    return str(status or '').strip().lower() in GOOD_STATUSES


def build_results_csv(rows: list[dict[str, Any]]) -> bytes:
    """
    rows: each dict has input_phone, response (XVerify JSON), optional error.
    """
    fieldnames = [
        'input_phone',
        'verified_phone',
        'status',
        'reason',
        'phone_type',
        'use_type',
        'is_valid',
        'error',
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        resp = row.get('response') or {}
        status = str(resp.get('status') or row.get('error') or '').strip()
        writer.writerow({
            'input_phone': row.get('input_phone', ''),
            'verified_phone': resp.get('phone', ''),
            'status': status,
            'reason': resp.get('reason', ''),
            'phone_type': resp.get('phone_type', ''),
            'use_type': resp.get('use_type', ''),
            'is_valid': 'yes' if is_valid_status(status) else 'no',
            'error': row.get('error', ''),
        })
    return buf.getvalue().encode('utf-8')


def good_phones_from_csv_bytes(csv_bytes: bytes) -> list[str]:
    import pandas as pd
    df = pd.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False).fillna('')
    if 'is_valid' not in df.columns:
        return []
    good = df[df['is_valid'].str.lower() == 'yes']
    col = 'verified_phone' if 'verified_phone' in good.columns else 'input_phone'
    return [str(x).strip() for x in good[col].tolist() if str(x).strip()]
