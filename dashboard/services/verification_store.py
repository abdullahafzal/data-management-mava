"""Per-email / per-phone verification results on LeadRecord rows."""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd
from django.utils import timezone

from pipeline.services.millionverifier import detect_status_column, normalize_category

from ..models import LeadRecord
from .record_extract import extract_all_emails, extract_all_phones, normalize_phone_key

MV_LABELS = {
    'good': 'Good',
    'invalid': 'Bad',
    'bad': 'Bad',
    'risky': 'Risky',
    'unknown': 'Unknown',
    'disposable': 'Disposable',
}


def _email_key(email: str) -> str:
    return (email or '').strip().lower()


def get_email_mv_category(record: LeadRecord, email: str) -> str:
    vd = record.verification_data or {}
    info = (vd.get('emails') or {}).get(_email_key(email)) or {}
    return str(info.get('millionverifier') or '').strip()


def has_mv_result(record: LeadRecord, email: str) -> bool:
    return bool(get_email_mv_category(record, email))


def mv_display_items(record: LeadRecord) -> list[dict[str, str]]:
    """
    One entry per email on the row for table rendering.
    Unverified emails show category 'pending' / label 'Unverified'.
    """
    columns = list(record.workspace.columns or []) if record.workspace_id else None
    emails = extract_all_emails(record.data, columns)
    if not emails:
        return []
    items = []
    for email in emails:
        cat = get_email_mv_category(record, email)
        if cat:
            label = MV_LABELS.get(cat, cat.replace('_', ' ').title())
            items.append({
                'email': email,
                'category': cat,
                'label': label,
            })
        else:
            items.append({
                'email': email,
                'category': 'pending',
                'label': 'Unverified',
            })
    return items


def sync_mv_gate_status(record: LeadRecord) -> None:
    columns = list(record.workspace.columns or []) if record.workspace_id else None
    emails = extract_all_emails(record.data, columns)
    if not emails:
        record.status_millionverifier = LeadRecord.ProcessStatus.PENDING
        return
    if all(has_mv_result(record, e) for e in emails):
        record.status_millionverifier = LeadRecord.ProcessStatus.PROCEEDED
    else:
        record.status_millionverifier = LeadRecord.ProcessStatus.PENDING


PHONE_GOOD_STATUSES = frozenset({'valid', 'ok', 'good'})


def get_phone_xverify_status(record: LeadRecord, phone: str) -> str:
    vd = record.verification_data or {}
    info = (vd.get('phones') or {}).get(normalize_phone_key(phone)) or {}
    return str(info.get('xverify') or '').strip().lower()


def has_xverify_result(record: LeadRecord, phone: str) -> bool:
    return bool(get_phone_xverify_status(record, phone))


def is_phone_xverify_valid(record: LeadRecord, phone: str) -> bool:
    return get_phone_xverify_status(record, phone) in PHONE_GOOD_STATUSES


def sync_xverify_gate_status(record: LeadRecord) -> None:
    columns = list(record.workspace.columns or []) if record.workspace_id else None
    phones = extract_all_phones(record.data, columns)
    if not phones:
        record.status_xverify = LeadRecord.ProcessStatus.PENDING
        return
    if all(has_xverify_result(record, p) for p in phones):
        record.status_xverify = LeadRecord.ProcessStatus.PROCEEDED
    else:
        record.status_xverify = LeadRecord.ProcessStatus.PENDING


def apply_xverify_results(
    records: list[LeadRecord],
    phone_statuses: dict[str, str],
) -> list[str]:
    """phone_key (digits) → xverify status; return updated public_ids."""
    now = timezone.now().isoformat()
    updated: list[str] = []
    for rec in records:
        columns = list(rec.workspace.columns or []) if rec.workspace_id else None
        row_phones = extract_all_phones(rec.data, columns)
        if not row_phones:
            continue
        vd: dict[str, Any] = dict(rec.verification_data or {})
        pmap: dict[str, Any] = dict(vd.get('phones') or {})
        changed = False
        for phone in row_phones:
            status = phone_statuses.get(normalize_phone_key(phone))
            if not status:
                continue
            pmap[normalize_phone_key(phone)] = {
                'xverify': status,
                'checked_at': now,
            }
            changed = True
        if changed:
            vd['phones'] = pmap
            rec.verification_data = vd
            sync_xverify_gate_status(rec)
            rec.sync_overall_process_status()
            rec.save(update_fields=[
                'verification_data',
                'status_xverify',
                'process_status',
                'updated_at',
            ])
            updated.append(rec.public_id)
    return updated


def parse_mv_report_csv(csv_bytes: bytes) -> dict[str, str]:
    """email (lower) → normalized category (good, invalid, risky, …)."""
    df = pd.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False).fillna('')
    if df.empty:
        return {}
    columns = [str(c) for c in df.columns.tolist()]
    email_col = None
    for c in columns:
        if c.strip().lower() in {'email', 'email_address', 'email address'}:
            email_col = c
            break
    if not email_col:
        email_col = columns[0]
    status_col = detect_status_column(columns)
    if not status_col:
        raise ValueError('Could not detect MillionVerifier status column in report.')

    out: dict[str, str] = {}
    for _, row in df.iterrows():
        email = str(row.get(email_col, '') or '').strip()
        if not email or '@' not in email:
            continue
        raw = str(row.get(status_col, '') or '').strip()
        out[_email_key(email)] = normalize_category(raw)
    return out


def apply_mv_results(
    records: list[LeadRecord],
    email_categories: dict[str, str],
) -> list[str]:
    """Write MV categories onto each row; return public_ids updated."""
    now = timezone.now().isoformat()
    updated: list[str] = []
    for rec in records:
        columns = list(rec.workspace.columns or []) if rec.workspace_id else None
        row_emails = extract_all_emails(rec.data, columns)
        if not row_emails:
            continue
        vd: dict[str, Any] = dict(rec.verification_data or {})
        emap: dict[str, Any] = dict(vd.get('emails') or {})
        changed = False
        for email in row_emails:
            cat = email_categories.get(_email_key(email))
            if not cat:
                continue
            emap[_email_key(email)] = {
                'millionverifier': cat,
                'checked_at': now,
            }
            changed = True
        if changed:
            vd['emails'] = emap
            rec.verification_data = vd
            sync_mv_gate_status(rec)
            rec.sync_overall_process_status()
            rec.save(update_fields=[
                'verification_data',
                'status_millionverifier',
                'process_status',
                'updated_at',
            ])
            updated.append(rec.public_id)
    return updated


def merge_verification_on_rematch(
    old_vd: dict | None,
    current_emails: list[str],
    current_phones: list[str] | None = None,
) -> dict:
    """
    After merge/rematch: keep verification only for emails/phones still on the row.
    New emails from a merged file start with no MV status (Unverified).
    """
    old_vd = old_vd or {}
    current_email_keys = {_email_key(e) for e in current_emails}
    current_phone_keys = {normalize_phone_key(p) for p in (current_phones or [])}

    new_emails = {
        k: v for k, v in (old_vd.get('emails') or {}).items()
        if k in current_email_keys
    }
    new_phones = {
        k: v for k, v in (old_vd.get('phones') or {}).items()
        if k in current_phone_keys
    }
    return {'emails': new_emails, 'phones': new_phones}


def snapshot_verification(record: LeadRecord) -> dict:
    return {
        **record.destination_statuses(),
        'verification_data': dict(record.verification_data or {}),
    }


def restore_verification_snapshot(record: LeadRecord, snap: dict) -> None:
    vd = snap.get('verification_data') or {}
    record.verification_data = vd
    for key, field, _label in LeadRecord.DESTINATION_FIELDS:
        if key in snap:
            setattr(record, field, snap[key])
    sync_mv_gate_status(record)
    sync_xverify_gate_status(record)
    record.sync_overall_process_status()
