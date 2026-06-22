"""Detect key and status columns in NY registry exports."""

from __future__ import annotations

import re

KEY_COLUMN_CANDIDATES = (
    'facility #',
    'facility number',
    'facility id',
    'facility no',
    'facility no.',
    'dos id',
    'dosid',
    'dos process id',
    'entity id',
    'entity number',
    'entity #',
    'filing number',
    'business id',
    'business entity id',
    'corp id',
    'id',
    'record id',
)

STATUS_COLUMN_CANDIDATES = (
    'status',
    'entity status',
    'business status',
    'status description',
    'current status',
    'entity status description',
)

CLOSED_STATUS_TOKENS = frozenset({
    'inactive',
    'dissolved',
    'closed',
    'cancelled',
    'canceled',
    'terminated',
    'withdrawn',
    'suspended',
    'revoked',
    'forfeited',
    'merged',
    'converted',
    'expired',
    'dead',
    'inactive/withdrawn',
})


def _norm_col(name: str) -> str:
    return re.sub(r'[\s_\-]+', ' ', str(name or '').strip().lower())


def detect_key_column(columns: list[str]) -> str | None:
    lowered = {_norm_col(c): c for c in columns}
    for candidate in KEY_COLUMN_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    for col in columns:
        n = _norm_col(col)
        if 'facility' in n and ('#' in col or 'number' in n or n.endswith(' id')):
            return col
        if 'dos' in n and 'id' in n:
            return col
        if 'entity' in n and ('number' in n or n.endswith(' id') or '#' in col):
            return col
    return None


def detect_status_column(columns: list[str]) -> str | None:
    lowered = {_norm_col(c): c for c in columns}
    for candidate in STATUS_COLUMN_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    for col in columns:
        if 'status' in _norm_col(col):
            return col
    return None


def is_closed_status(value: str) -> bool:
    text = re.sub(r'[\s_\-]+', ' ', str(value or '').strip().lower())
    if not text:
        return False
    if text in CLOSED_STATUS_TOKENS:
        return True
    return any(tok in text for tok in ('dissolved', 'inactive', 'terminated', 'withdrawn'))
