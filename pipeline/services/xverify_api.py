from __future__ import annotations

from typing import Any

import requests


class XVerifyError(Exception):
    pass


BASE_URL = "https://api.xverify.com/v2/pv"


def verify_phone(api_key: str, domain: str, phone: str, *, timeout_seconds: int = 20) -> dict[str, Any]:
    if not api_key or not api_key.strip():
        raise XVerifyError("XVerify API key is missing.")
    if not domain or not domain.strip():
        raise XVerifyError("XVerify domain is missing (set XVERIFY_DOMAIN).")
    phone = (phone or "").strip()
    if not phone:
        raise XVerifyError("Phone number is missing.")

    try:
        r = requests.get(
            BASE_URL,
            params={
                "phone": phone,
                "api_key": api_key.strip(),
                "domain": domain.strip(),
            },
            timeout=timeout_seconds,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise XVerifyError(str(exc)) from exc
    except ValueError as exc:
        raise XVerifyError(f"Invalid JSON response from XVerify: {exc}") from exc

