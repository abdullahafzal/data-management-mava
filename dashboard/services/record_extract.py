"""Pull emails / phones from LeadRecord.data for pipeline actions."""

from __future__ import annotations

import re

from ..models import LeadRecord

EMAIL_HINTS = (
    'email', 'e-mail', 'mail', 'emails',
)
PHONE_HINTS = (
    'phone', 'mobile', 'cell', 'tel', 'fax',
)


def _norm(s: str) -> str:
    return re.sub(r'[\s_\-]+', ' ', (s or '').strip().lower())


def _column_score(col: str, hints: tuple[str, ...]) -> int | None:
    n = _norm(col)
    if not n:
        return None
    if n in hints:
        return 0
    for i, h in enumerate(hints):
        if h in n.split():
            return 1 + i
        if h in n:
            return 10 + i
    return None


def _pick_column(columns: list[str], hints: tuple[str, ...]) -> str | None:
    scored: list[tuple[int, str]] = []
    for col in columns:
        score = _column_score(col, hints)
        if score is not None:
            scored.append((score, col))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1].lower()))
    return scored[0][1]


def _first_email_value(raw: str) -> str:
    for part in re.split(r'[|,;]', raw or ''):
        val = part.strip()
        if val and '@' in val:
            return val
    return ''


def _first_phone_value(raw: str) -> str:
    for part in re.split(r'[|,;]', raw or ''):
        val = part.strip()
        digits = re.sub(r'\D', '', val)
        if len(digits) >= 10:
            return val
    return ''


def normalize_phone_key(phone: str) -> str:
    return re.sub(r'\D', '', phone or '')


def _all_values_from_columns(
    data: dict | None,
    columns: list[str] | None,
    hints: tuple[str, ...],
    value_fn,
    *,
    skip_type_cols: bool = False,
) -> list[str]:
    data = data or {}
    cols = columns or list(data.keys())
    scored: list[tuple[int, str]] = []
    for col in cols:
        score = _column_score(col, hints)
        if score is None:
            continue
        if skip_type_cols and 'type' in _norm(col):
            continue
        scored.append((score, col))
    scored.sort(key=lambda t: (t[0], t[1].lower()))

    seen: set[str] = set()
    out: list[str] = []
    for _score, col in scored:
        raw = str(data.get(col, '') or '')
        for part in re.split(r'[|,;]', raw):
            val = value_fn(part.strip())
            if not val:
                continue
            key = val.lower() if '@' in val else normalize_phone_key(val)
            if key in seen:
                continue
            seen.add(key)
            out.append(val)
    return out


def extract_all_emails(data: dict | None, columns: list[str] | None = None) -> list[str]:
    def pick(part: str) -> str:
        return part if part and '@' in part else ''

    return _all_values_from_columns(data, columns, EMAIL_HINTS, pick)


def extract_all_phones(data: dict | None, columns: list[str] | None = None) -> list[str]:
    def pick(part: str) -> str:
        digits = re.sub(r'\D', '', part)
        return part if len(digits) >= 10 else ''

    return _all_values_from_columns(
        data, columns, PHONE_HINTS, pick, skip_type_cols=True,
    )


def extract_email(data: dict | None, columns: list[str] | None = None) -> str:
    emails = extract_all_emails(data, columns)
    return emails[0] if emails else ''


def extract_phone(data: dict | None, columns: list[str] | None = None) -> str:
    phones = extract_all_phones(data, columns)
    return phones[0] if phones else ''


def collect_emails_for_mv(
    records: list[LeadRecord],
    *,
    limit: int,
) -> list[tuple[str, str]]:
    """
    (public_id, email) pairs for MillionVerifier.
    Skips emails already verified on that row; caps at limit across selection.
    """
    from .verification_store import has_mv_result

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    cols_cache: dict[int, list[str] | None] = {}
    for rec in records:
        if rec.workspace_id not in cols_cache:
            cols_cache[rec.workspace_id] = list(rec.workspace.columns or []) if rec.workspace_id else None
        columns = cols_cache[rec.workspace_id]
        for email in extract_all_emails(rec.data, columns):
            key = email.lower()
            if key in seen:
                continue
            if has_mv_result(rec, email):
                continue
            seen.add(key)
            out.append((rec.public_id, email))
            if len(out) >= limit:
                return out
    return out


def collect_emails(
    records: list[LeadRecord],
    *,
    limit: int,
    good_only: bool = False,
) -> list[tuple[str, str]]:
    """(public_id, email) pairs; optional good_only uses MV good results."""
    from .verification_store import get_email_mv_category

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    cols_cache: dict[int, list[str] | None] = {}
    for rec in records:
        if rec.workspace_id not in cols_cache:
            cols_cache[rec.workspace_id] = list(rec.workspace.columns or []) if rec.workspace_id else None
        columns = cols_cache[rec.workspace_id]
        for email in extract_all_emails(rec.data, columns):
            if good_only and get_email_mv_category(rec, email) != 'good':
                continue
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((rec.public_id, email))
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out


def collect_phones_for_xverify(
    records: list[LeadRecord],
    *,
    limit: int,
) -> list[tuple[str, str]]:
    """(public_id, phone) for phones not yet XVerify-checked."""
    from .verification_store import has_xverify_result

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    cols_cache: dict[int, list[str] | None] = {}
    for rec in records:
        if rec.workspace_id not in cols_cache:
            cols_cache[rec.workspace_id] = list(rec.workspace.columns or []) if rec.workspace_id else None
        columns = cols_cache[rec.workspace_id]
        for phone in extract_all_phones(rec.data, columns):
            if has_xverify_result(rec, phone):
                continue
            digits = normalize_phone_key(phone)
            if digits in seen:
                continue
            seen.add(digits)
            out.append((rec.public_id, phone))
            if len(out) >= limit:
                return out
    return out


def collect_phones_for_simpletexting(
    records: list[LeadRecord],
    *,
    limit: int,
) -> list[tuple[str, str, str]]:
    """(public_id, phone, email) — only XVerify-valid phones."""
    from .verification_store import is_phone_xverify_valid

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    cols_cache: dict[int, list[str] | None] = {}
    for rec in records:
        if rec.workspace_id not in cols_cache:
            cols_cache[rec.workspace_id] = list(rec.workspace.columns or []) if rec.workspace_id else None
        columns = cols_cache[rec.workspace_id]
        good_email = ''
        for email in extract_all_emails(rec.data, columns):
            from .verification_store import get_email_mv_category
            if get_email_mv_category(rec, email) == 'good':
                good_email = email
                break
        for phone in extract_all_phones(rec.data, columns):
            if not is_phone_xverify_valid(rec, phone):
                continue
            digits = normalize_phone_key(phone)
            if digits in seen:
                continue
            seen.add(digits)
            out.append((rec.public_id, phone, good_email))
            if len(out) >= limit:
                return out
    return out


def collect_phones(
    records: list[LeadRecord],
    *,
    limit: int,
) -> list[tuple[str, str]]:
    """(public_id, phone) pairs, deduped by digits, capped at limit."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    cols_cache: dict[int, list[str] | None] = {}
    for rec in records:
        if rec.workspace_id not in cols_cache:
            cols_cache[rec.workspace_id] = list(rec.workspace.columns or []) if rec.workspace_id else None
        columns = cols_cache[rec.workspace_id]
        for phone in extract_all_phones(rec.data, columns):
            digits = normalize_phone_key(phone)
            if digits in seen:
                continue
            seen.add(digits)
            out.append((rec.public_id, phone))
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out
