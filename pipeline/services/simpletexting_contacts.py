"""Build SimpleTexting contact rows from XVerify or MillionVerifier good exports."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..models import DataImport, PhoneVerificationJob, VerificationJob
from .simpletexting_api import normalize_phone
from .xverify_results import good_phones_from_csv_bytes

EMAIL_COLUMNS = ('email', 'email_1', 'email_2', 'email_3', 'email_address', 'email address')
PHONE_COLUMNS = ('phone', 'phone_1', 'phone_2', 'phone_3', 'contact_phone', 'mobile')


def _find_column(columns, candidates: tuple[str, ...]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in columns}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    return None


def _first_phone_from_row(row, phone_cols: list[str]) -> str:
    for col in phone_cols:
        val = str(row.get(col, '')).strip()
        if val:
            normalized = normalize_phone(val)
            if normalized:
                return normalized
    return ''


def _first_email_from_row(row, email_cols: list[str]) -> str:
    for col in email_cols:
        val = str(row.get(col, '')).strip()
        if val and '@' in val:
            return val
    return ''


def contacts_from_xverify_results(csv_bytes: bytes) -> list[dict[str, str]]:
    phones = good_phones_from_csv_bytes(csv_bytes)
    return [{'phone': normalize_phone(p), 'email': ''} for p in phones if normalize_phone(p)]


def contacts_from_mv_good_export(
    good_path: str | Path,
    cleaned_path: str | Path,
) -> list[dict[str, str]]:
    """
    MillionVerifier good emails + phones from the good file or cleaned export (by email).
    SimpleTexting still requires a phone on each contact.
    """
    good_df = pd.read_csv(good_path, dtype=str, keep_default_na=False).fillna('')
    cleaned_df = pd.read_csv(cleaned_path, dtype=str, keep_default_na=False).fillna('')

    good_email_cols = [
        c for c in good_df.columns
        if str(c).strip().lower() in EMAIL_COLUMNS
    ]
    good_phone_cols = [
        c for c in good_df.columns
        if str(c).strip().lower() in PHONE_COLUMNS
    ]
    cleaned_email_cols = [
        c for c in cleaned_df.columns
        if str(c).strip().lower() in EMAIL_COLUMNS
    ]
    cleaned_phone_cols = [
        c for c in cleaned_df.columns
        if str(c).strip().lower() in PHONE_COLUMNS
    ]

    phone_by_email: dict[str, str] = {}
    if cleaned_email_cols and cleaned_phone_cols:
        for _, row in cleaned_df.iterrows():
            email = _first_email_from_row(row, cleaned_email_cols).lower()
            phone = _first_phone_from_row(row, cleaned_phone_cols)
            if email and phone and email not in phone_by_email:
                phone_by_email[email] = phone

    contacts: list[dict[str, str]] = []
    seen_phones: set[str] = set()

    if not good_email_cols:
        return contacts

    for _, row in good_df.iterrows():
        email = _first_email_from_row(row, good_email_cols)
        if not email:
            continue
        phone = _first_phone_from_row(row, good_phone_cols) if good_phone_cols else ''
        if not phone:
            phone = phone_by_email.get(email.lower(), '')
        if not phone or phone in seen_phones:
            continue
        seen_phones.add(phone)
        contacts.append({'phone': phone, 'email': email})

    return contacts


def resolve_simpletexting_source(data_import: DataImport, *, xverify_configured: bool) -> str:
    """
    Return push source: 'xverify' | 'mv_good' | '' (not ready).
    When XVerify is unavailable, fall back to MillionVerifier good emails for testing.
    """
    cleaned = getattr(data_import, 'cleaned_dataset', None)
    if not cleaned or not cleaned.file:
        return ''

    phone_job = getattr(cleaned, 'phone_verification_job', None)
    if (
        xverify_configured
        and phone_job
        and phone_job.status == PhoneVerificationJob.Status.COMPLETED
        and phone_job.results_file
    ):
        return 'xverify'

    mv_job = getattr(cleaned, 'verification_job', None)
    if not mv_job or mv_job.status != VerificationJob.Status.COMPLETED:
        return ''

    good = mv_job.exports.filter(category='good').first()
    if not good or not good.file:
        return ''

    if xverify_configured:
        return ''

    return 'mv_good'


def collect_simpletexting_contacts(
    data_import: DataImport,
    *,
    source: str,
) -> list[dict[str, str]]:
    cleaned = getattr(data_import, 'cleaned_dataset', None)
    if not cleaned:
        return []

    if source == 'xverify':
        job = getattr(cleaned, 'phone_verification_job', None)
        if not job or not job.results_file:
            return []
        with job.results_file.open('rb') as f:
            return contacts_from_xverify_results(f.read())

    if source == 'mv_good':
        mv_job = getattr(cleaned, 'verification_job', None)
        if not mv_job:
            return []
        good = mv_job.exports.filter(category='good').first()
        if not good or not good.file:
            return []
        return contacts_from_mv_good_export(good.file.path, cleaned.file.path)

    return []
