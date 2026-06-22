"""Small-token OpenAI summary for NY registry diff reports."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = 'https://api.openai.com/v1/chat/completions'
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 5, 10)


class RegistryAIError(Exception):
    pass


SYSTEM_PROMPT = """You are an analyst for MAVA Advisors NY State business registry weekly syncs.

You receive a SMALL pre-computed diff summary (not raw files). Your job:
- Explain the data structure (key columns, status field if present)
- Summarize what changed this week in plain English
- Highlight active vs closed/inactive counts when available
- Flag anything unusual (large owner-name churn, many removals, duplicate keys)
- Recommend whether the team should review before approving the baseline

Rules:
- Never invent counts — only use numbers from the JSON context
- Be concise and actionable
- Return ONLY valid JSON (no markdown fences):
{
  "headline": "under 100 chars",
  "summary": "2-4 sentences",
  "structure_notes": ["bullet about columns/schema"],
  "change_highlights": ["bullet 1", "bullet 2"],
  "warnings": ["optional"],
  "recommendation": "approve" | "review_carefully" | "hold",
  "confidence": "high" | "medium" | "low"
}
"""


def _friendly_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        message = str((payload.get('error') or {}).get('message') or '')
    except ValueError:
        message = response.text[:300]
    if response.status_code == 429:
        return 'OpenAI rate limit — wait and try again.'
    if response.status_code == 401:
        return 'OpenAI API key rejected (401).'
    return message or f'OpenAI error ({response.status_code})'


def analyze_registry_diff(
    api_key: str,
    diff_stats: dict[str, Any],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    if not api_key or not api_key.strip():
        raise RegistryAIError('OpenAI API key is missing (OPENAI_API_KEY).')

    model = model or getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini')
    user_content = (
        'Analyze this NY registry weekly diff and respond with JSON only:\n\n'
        + json.dumps(diff_stats, indent=2, default=str)
    )
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                OPENAI_CHAT_URL,
                headers={
                    'Authorization': f'Bearer {api_key.strip()}',
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=90,
            )
        except requests.RequestException as exc:
            raise RegistryAIError(str(exc)) from exc

        if r.status_code == 429 and attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF_SECONDS[attempt])
            continue
        if not r.ok:
            raise RegistryAIError(_friendly_error(r))

        try:
            content = r.json()['choices'][0]['message']['content']
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise RegistryAIError(f'Unexpected OpenAI response: {exc}') from exc

        if not isinstance(parsed, dict):
            raise RegistryAIError('OpenAI did not return a JSON object.')

        return {'parsed': parsed, 'model': model}

    raise RegistryAIError('OpenAI rate limit persisted after retries.')
