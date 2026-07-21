"""Detect and run the next pipeline step for selected lead rows."""

from __future__ import annotations

from django.conf import settings

from ..models import LeadRecord
from .record_extract import extract_all_emails, extract_all_phones
from .verification_store import (
    get_email_mv_category,
    has_mv_result,
    has_xverify_result,
    is_phone_xverify_valid,
)


def xverifier_process_enabled() -> bool:
    return bool(getattr(settings, 'XVERIFIER_PROCESS', False))


def _columns(record: LeadRecord) -> list[str] | None:
    if record.workspace_id:
        return list(record.workspace.columns or [])
    return None


def row_needs_millionverifier(record: LeadRecord) -> bool:
    emails = extract_all_emails(record.data, _columns(record))
    if not emails:
        return False
    return any(not has_mv_result(record, e) for e in emails)


def row_needs_smartlead(record: LeadRecord) -> bool:
    """All emails MV-checked; at least one good; not yet sent to Smartlead."""
    if record.status_smartlead == LeadRecord.ProcessStatus.PROCEEDED:
        return False
    emails = extract_all_emails(record.data, _columns(record))
    if not emails:
        return False
    if any(not has_mv_result(record, e) for e in emails):
        return False
    return any(get_email_mv_category(record, e) == 'good' for e in emails)


def row_needs_xverify(record: LeadRecord) -> bool:
    if not xverifier_process_enabled():
        return False
    phones = extract_all_phones(record.data, _columns(record))
    if not phones:
        return False
    return any(not has_xverify_result(record, p) for p in phones)


def row_needs_simpletexting(record: LeadRecord) -> bool:
    """All phones XVerify-checked; at least one valid; not yet sent to SimpleTexting."""
    if not xverifier_process_enabled():
        return False
    if record.status_simpletexting == LeadRecord.ProcessStatus.PROCEEDED:
        return False
    phones = extract_all_phones(record.data, _columns(record))
    if not phones:
        return False
    if any(not has_xverify_result(record, p) for p in phones):
        return False
    return any(is_phone_xverify_valid(record, p) for p in phones)


def process_next_order() -> tuple[tuple[str, str], ...]:
    """MV → Smartlead (good emails). Phone steps only when XVERIFIER_PROCESS=true."""
    steps: list[tuple[str, str]] = [
        ('millionverifier', 'MillionVerifier'),
        ('smartlead', 'Smartlead (good emails)'),
    ]
    if xverifier_process_enabled():
        steps.extend([
            ('xverify', 'Phone Verifier (XVerify)'),
            ('simpletexting', 'SimpleTexting (valid phones)'),
        ])
    return tuple(steps)


NEEDS_FN = {
    'millionverifier': row_needs_millionverifier,
    'smartlead': row_needs_smartlead,
    'xverify': row_needs_xverify,
    'simpletexting': row_needs_simpletexting,
}


def detect_next_step(records: list[LeadRecord]) -> tuple[str, str] | None:
    if not records:
        return None
    for key, label in process_next_order():
        fn = NEEDS_FN.get(key)
        if fn and any(fn(r) for r in records):
            return key, label
    return None


def channel_updates_for_process_next(records: list[LeadRecord]) -> dict[str, str]:
    found = detect_next_step(records)
    if not found:
        msg = (
            'Nothing to process — emails may need MillionVerifier first, '
            'or good emails already sent to Smartlead.'
        )
        if xverifier_process_enabled():
            msg += ' Phone path: XVerify then SimpleTexting.'
        else:
            msg += ' Phone steps are off (set XVERIFIER_PROCESS=true in .env when ready).'
        raise ValueError(msg)
    key, _label = found
    return {key: LeadRecord.ProcessStatus.PROCEEDED}


def preview_next_step(records: list[LeadRecord]) -> str:
    found = detect_next_step(records)
    return found[1] if found else ''
