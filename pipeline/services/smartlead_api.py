from __future__ import annotations

from typing import Any

import requests


class SmartleadError(Exception):
    pass


BASE_URL = "https://server.smartlead.ai/api/v1"


def create_campaign(api_key: str, name: str, *, client_id: str | None = None) -> dict[str, Any]:
    if not api_key or not api_key.strip():
        raise SmartleadError("Smartlead API key is missing.")
    url = f"{BASE_URL}/campaigns/create"
    payload: dict[str, Any] = {"name": name}
    if client_id:
        payload["client_id"] = client_id
    try:
        r = requests.post(url, params={"api_key": api_key.strip()}, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise SmartleadError(str(exc)) from exc
    except ValueError as exc:
        raise SmartleadError(f"Invalid JSON response from Smartlead: {exc}") from exc


def add_leads(api_key: str, campaign_id: int | str, lead_list: list[dict[str, Any]]) -> dict[str, Any]:
    if not api_key or not api_key.strip():
        raise SmartleadError("Smartlead API key is missing.")
    url = f"{BASE_URL}/campaigns/{campaign_id}/leads"
    payload: dict[str, Any] = {"lead_list": lead_list}
    try:
        r = requests.post(url, params={"api_key": api_key.strip()}, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise SmartleadError(str(exc)) from exc
    except ValueError as exc:
        raise SmartleadError(f"Invalid JSON response from Smartlead: {exc}") from exc

