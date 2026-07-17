"""Build GoHighLevel contact rows from MillionVerifier and XVerify exports."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..models import DataImport, PhoneVerificationJob, VerificationJob
from .ghl_api import is_valid_email, is_valid_phone
from .simpletexting_api import normalize_phone
from .simpletexting_contacts import (
    EMAIL_COLUMNS,
    PHONE_COLUMNS,
    _find_column,
    _first_email_from_row,
    _first_phone_from_row,
    collect_simpletexting_contacts,
    resolve_simpletexting_source,
)

NAME_COLUMNS = (
    "name",
    "full_name",
    "full name",
    "owner name",
    "owner name ",
    "contact_name",
    "first_name",
    "first name",
)
COMPANY_COLUMNS = ("company", "company_name", "company name", "business_name", "facility name")


def _split_name(raw: str) -> tuple[str, str]:
    parts = [p for p in str(raw or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _enrich_from_cleaned_row(row, contact: dict[str, str]) -> dict[str, str]:
    out = dict(contact)
    first_col = _find_column(row.index, ("first_name", "first name"))
    last_col = _find_column(row.index, ("last_name", "last name"))
    if first_col:
        out["first_name"] = str(row.get(first_col, "")).strip() or out.get("first_name", "")
    if last_col:
        out["last_name"] = str(row.get(last_col, "")).strip() or out.get("last_name", "")

    if not out.get("first_name") and not out.get("last_name"):
        name_col = _find_column(row.index, NAME_COLUMNS)
        if name_col:
            first, last = _split_name(str(row.get(name_col, "")))
            out["first_name"] = first
            out["last_name"] = last

    company_col = _find_column(row.index, COMPANY_COLUMNS)
    if company_col:
        out["company"] = str(row.get(company_col, "")).strip()

    return out


def _lookup_cleaned_row(cleaned_df: pd.DataFrame, *, email: str = "", phone: str = "") -> pd.Series | None:
    email_cols = [c for c in cleaned_df.columns if str(c).strip().lower() in EMAIL_COLUMNS]
    phone_cols = [c for c in cleaned_df.columns if str(c).strip().lower() in PHONE_COLUMNS]

    email_l = (email or "").strip().lower()
    phone_n = normalize_phone(phone) if phone else ""

    for _, row in cleaned_df.iterrows():
        if email_l and email_cols:
            for col in email_cols:
                if str(row.get(col, "")).strip().lower() == email_l:
                    return row
        if phone_n and phone_cols:
            for col in phone_cols:
                if normalize_phone(str(row.get(col, ""))) == phone_n:
                    return row
    return None


def _emails_from_mv_good(good_path: str | Path) -> list[str]:
    df = pd.read_csv(good_path, dtype=str, keep_default_na=False).fillna("")
    email_cols = [c for c in df.columns if str(c).strip().lower() in EMAIL_COLUMNS]
    if not email_cols:
        col = df.columns[0] if len(df.columns) else None
        email_cols = [col] if col else []

    emails: list[str] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        email = _first_email_from_row(row, email_cols)
        if not is_valid_email(email):
            continue
        key = email.lower()
        if email and key not in seen:
            seen.add(key)
            emails.append(email)
    return emails


def _merge_contact(target: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    out = dict(target)
    for key in ("phone", "email", "first_name", "last_name", "company"):
        if not out.get(key) and incoming.get(key):
            out[key] = incoming[key]
    return out


def collect_ghl_contacts(
    data_import: DataImport,
    *,
    xverify_configured: bool,
) -> list[dict[str, str]]:
    """
    Contacts for GoHighLevel upsert — phones from XVerify / MV+cleaned match,
    plus MillionVerifier good emails (email-only when no phone).
    """
    cleaned = getattr(data_import, "cleaned_dataset", None)
    cleaned_df = None
    if cleaned and cleaned.file:
        cleaned_df = pd.read_csv(cleaned.file.path, dtype=str, keep_default_na=False).fillna("")

    by_key: dict[str, dict[str, str]] = {}

    def add_contact(contact: dict[str, str]) -> None:
        email = (contact.get("email") or "").strip()
        phone = normalize_phone(contact.get("phone") or "")
        if email and not is_valid_email(email):
            email = ""
        if phone and not is_valid_phone(phone):
            phone = ""
        if not email and not phone:
            return
        key = email.lower() if email else phone
        entry = {
            "email": email,
            "phone": phone,
            "first_name": contact.get("first_name", ""),
            "last_name": contact.get("last_name", ""),
            "company": contact.get("company", ""),
        }
        if key in by_key:
            by_key[key] = _merge_contact(by_key[key], entry)
        else:
            by_key[key] = entry

    st_source = resolve_simpletexting_source(data_import, xverify_configured=xverify_configured)
    if st_source:
        for row in collect_simpletexting_contacts(data_import, source=st_source):
            contact = {"email": row.get("email", ""), "phone": row.get("phone", "")}
            if cleaned_df is not None:
                matched = _lookup_cleaned_row(
                    cleaned_df,
                    email=contact["email"],
                    phone=contact["phone"],
                )
                if matched is not None:
                    contact = _enrich_from_cleaned_row(matched, contact)
            add_contact(contact)

    mv_job = getattr(cleaned, "verification_job", None) if cleaned else None
    if mv_job and mv_job.status == VerificationJob.Status.COMPLETED:
        good = mv_job.exports.filter(category="good").first()
        if good and good.file:
            for email in _emails_from_mv_good(good.file.path):
                contact: dict[str, str] = {"email": email, "phone": ""}
                if cleaned_df is not None:
                    matched = _lookup_cleaned_row(cleaned_df, email=email)
                    if matched is not None:
                        contact = _enrich_from_cleaned_row(matched, contact)
                        phone = _first_phone_from_row(
                            matched,
                            [
                                c for c in cleaned_df.columns
                                if str(c).strip().lower() in PHONE_COLUMNS
                            ],
                        )
                        if phone:
                            contact["phone"] = phone
                add_contact(contact)

    return list(by_key.values())


def describe_ghl_sources(data_import: DataImport, *, xverify_configured: bool) -> str:
    """Short UI hint for which data will be pushed."""
    cleaned = getattr(data_import, "cleaned_dataset", None)
    if not cleaned:
        return ""

    parts: list[str] = []
    st_source = resolve_simpletexting_source(data_import, xverify_configured=xverify_configured)
    if st_source == "xverify":
        parts.append("XVerify valid phones")
    elif st_source == "mv_good":
        parts.append("MillionVerifier good emails matched to phones")

    mv_job = getattr(cleaned, "verification_job", None)
    if mv_job and mv_job.status == VerificationJob.Status.COMPLETED:
        good = mv_job.exports.filter(category="good").first()
        if good and good.file:
            parts.append("MillionVerifier good emails")

    if not parts:
        if xverify_configured:
            return "Run XVerify (step 4) or MillionVerifier (step 3) first."
        return "Run MillionVerifier (step 3) first."

    return " + ".join(dict.fromkeys(parts))


def ghl_push_ready(data_import: DataImport, *, xverify_configured: bool, ghl_configured: bool) -> bool:
    if not ghl_configured:
        return False
    cleaned = getattr(data_import, "cleaned_dataset", None)
    if not cleaned or not cleaned.file:
        return False
    mv_job = getattr(cleaned, "verification_job", None)
    phone_job = getattr(cleaned, "phone_verification_job", None)
    if mv_job and mv_job.status == VerificationJob.Status.COMPLETED:
        return True
    if (
        xverify_configured
        and phone_job
        and phone_job.status == PhoneVerificationJob.Status.COMPLETED
    ):
        return True
    return False
