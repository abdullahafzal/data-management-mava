from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import requests


class SimpleTextingError(Exception):
    pass


BASE_URL = "https://api-app2.simpletexting.com/v2/api"


def _headers(api_key: str) -> dict[str, str]:
    if not api_key or not api_key.strip():
        raise SimpleTextingError("SimpleTexting API key is missing.")
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def normalize_phone(phone: str) -> str:
    """SimpleTexting expects digits (typically 10-digit US)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def _list_id_from_payload(data: dict[str, Any]) -> str:
    return str(data.get("listId") or data.get("id") or "").strip()


def get_list(api_key: str, list_id_or_name: str) -> str | None:
    """Return list id if the list exists (by id or exact name)."""
    encoded = quote(list_id_or_name.strip(), safe="")
    url = f"{BASE_URL}/contact-lists/{encoded}"
    try:
        r = requests.get(url, headers=_headers(api_key), timeout=60)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise SimpleTextingError(str(exc)) from exc
    except ValueError as exc:
        raise SimpleTextingError(f"Invalid JSON response from SimpleTexting: {exc}") from exc
    list_id = _list_id_from_payload(data)
    return list_id or None


def find_list_by_name(api_key: str, name: str) -> str | None:
    """Paginate contact lists and match by name (case-insensitive)."""
    target = name.strip().casefold()
    page = 0
    while page < 50:
        try:
            r = requests.get(
                f"{BASE_URL}/contact-lists",
                headers=_headers(api_key),
                params={"page": page, "size": 100},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as exc:
            raise SimpleTextingError(str(exc)) from exc
        except ValueError as exc:
            raise SimpleTextingError(f"Invalid JSON response from SimpleTexting: {exc}") from exc

        for item in data.get("content") or []:
            item_name = str(item.get("name") or "").strip().casefold()
            if item_name == target:
                list_id = _list_id_from_payload(item)
                if list_id:
                    return list_id
        total_pages = int(data.get("totalPages") or 0)
        if page + 1 >= total_pages:
            break
        page += 1
    return None


def create_list(api_key: str, name: str) -> str:
    url = f"{BASE_URL}/contact-lists"
    try:
        r = requests.post(url, headers=_headers(api_key), json={"name": name}, timeout=60)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise SimpleTextingError(str(exc)) from exc
    except ValueError as exc:
        raise SimpleTextingError(f"Invalid JSON response from SimpleTexting: {exc}") from exc

    list_id = _list_id_from_payload(data)
    if not list_id:
        raise SimpleTextingError(f"SimpleTexting did not return list id. Response: {data}")
    return list_id


def get_or_create_list(api_key: str, name: str) -> tuple[str, bool]:
    """
    Return (list_id, created_new).
    Reuses an existing list when the campaign name already exists (409 on create).
    """
    name = name.strip()[:41]
    if not name:
        raise SimpleTextingError("List name is required.")

    existing = get_list(api_key, name) or find_list_by_name(api_key, name)
    if existing:
        return existing, False

    try:
        return create_list(api_key, name), True
    except SimpleTextingError as exc:
        if "409" not in str(exc):
            raise
        existing = get_list(api_key, name) or find_list_by_name(api_key, name)
        if existing:
            return existing, False
        raise SimpleTextingError(
            f'List "{name}" already exists in SimpleTexting but could not be fetched. '
            f'Original error: {exc}'
        ) from exc


def create_contact_on_lists(
    api_key: str,
    phone: str,
    list_ids: list[str],
    *,
    upsert: bool = True,
    lists_replacement: bool = False,
) -> dict[str, Any]:
    """
    Create (or update) a contact and add them to one or more lists.

    See: POST /api/contacts — do not use contact-lists/.../contacts unless the
    contact already exists in SimpleTexting.
    """
    contact_phone = normalize_phone(phone)
    if not contact_phone:
        raise SimpleTextingError("Phone number is missing or invalid.")

    url = f"{BASE_URL}/contacts"
    params = {
        "upsert": str(upsert).lower(),
        "listsReplacement": str(lists_replacement).lower(),
    }
    payload = {
        "contactPhone": contact_phone,
        "listIds": list_ids,
    }
    try:
        r = requests.post(
            url,
            headers=_headers(api_key),
            params=params,
            json=payload,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise SimpleTextingError(str(exc)) from exc
    except ValueError as exc:
        raise SimpleTextingError(f"Invalid JSON response from SimpleTexting: {exc}") from exc


def add_contact_to_list(api_key: str, list_id_or_name: str, contact_phone_or_id: str) -> dict[str, Any]:
    """Backward-compatible wrapper: creates/updates contact on the given list."""
    return create_contact_on_lists(
        api_key,
        contact_phone_or_id,
        [list_id_or_name],
    )
