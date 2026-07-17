"""GoHighLevel (LeadConnector) contacts API."""

from __future__ import annotations

import re
from typing import Any

import requests


class GoHighLevelError(Exception):
    pass


BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
)


def is_valid_email(email: str) -> bool:
    e = (email or "").strip()
    return bool(e and _EMAIL_RE.match(e))


def is_valid_phone(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return len(digits) == 10


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def format_ghl_phone(phone: str) -> str:
    """Normalize to E.164 US (+1XXXXXXXXXX) when possible."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1{digits}"
    if phone and str(phone).strip().startswith("+"):
        return str(phone).strip()
    return digits or str(phone or "").strip()


def upsert_contact(
    api_key: str,
    location_id: str,
    *,
    email: str = "",
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    company_name: str = "",
    tags: list[str] | None = None,
    source: str = "Data Management MAVA",
) -> dict[str, Any]:
    """
    Create or update a contact in a GHL sub-account (POST /contacts/upsert).
    At least one of email or phone is required.
    """
    if not api_key or not api_key.strip():
        raise GoHighLevelError("GoHighLevel API key is missing.")
    if not location_id or not location_id.strip():
        raise GoHighLevelError("GoHighLevel location ID is missing.")

    email = (email or "").strip()
    phone_raw = (phone or "").strip()
    phone = format_ghl_phone(phone_raw) if phone_raw else ""

    if email and not is_valid_email(email):
        email = ""
    if phone and not is_valid_phone(phone):
        phone = ""

    if not email and not phone:
        raise GoHighLevelError("Contact must have a valid email or US phone number.")

    payload: dict[str, Any] = {
        "locationId": location_id.strip(),
        "source": source,
    }
    if email:
        payload["email"] = email
    if phone:
        payload["phone"] = phone
    if first_name and first_name.strip():
        payload["firstName"] = first_name.strip()
    if last_name and last_name.strip():
        payload["lastName"] = last_name.strip()
    if company_name and company_name.strip():
        payload["companyName"] = company_name.strip()
    if tags:
        payload["tags"] = [t.strip() for t in tags if t and str(t).strip()]

    url = f"{BASE_URL}/contacts/upsert"
    try:
        r = requests.post(url, headers=_headers(api_key), json=payload, timeout=60)
        if r.status_code >= 400:
            detail = r.text[:500] if r.text else r.reason
            raise GoHighLevelError(f"HTTP {r.status_code}: {detail}")
        return r.json()
    except requests.RequestException as exc:
        raise GoHighLevelError(str(exc)) from exc
    except ValueError as exc:
        raise GoHighLevelError(f"Invalid JSON response from GoHighLevel: {exc}") from exc
