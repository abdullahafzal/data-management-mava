"""
MillionVerifier real-time API (single email).

Docs: https://developer.millionverifier.com/

Bulk verification uses a separate flow (upload/job); not implemented here yet.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

SINGLE_VERIFY_URL = 'https://api.millionverifier.com/emailverifier'


class MillionVerifierError(Exception):
    pass


def verify_email_realtime(api_key: str, email: str, timeout_seconds: int = 20) -> dict[str, Any]:
    """Call MillionVerifier realtime endpoint. Raises on HTTP error."""
    if not api_key.strip():
        raise MillionVerifierError('MillionVerifier API key is missing.')
    try:
        r = requests.get(
            SINGLE_VERIFY_URL,
            params={
                'api': api_key,
                'email': email.strip(),
                'timeout': timeout_seconds,
            },
            timeout=timeout_seconds + 10,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        logger.warning('MillionVerifier request failed: %s', exc)
        raise MillionVerifierError(str(exc)) from exc
