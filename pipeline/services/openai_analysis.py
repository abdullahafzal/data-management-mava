"""OpenAI-powered analysis of Outscraper filter overlap in the database."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = 'https://api.openai.com/v1/chat/completions'
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 5, 10)


class OpenAIAnalysisError(Exception):
    pass


SYSTEM_PROMPT = """You are an analyst for an internal B2B lead pipeline tool.
The team imports Google Maps business data from Outscraper (paid per scrape).
Your job: compare PROPOSED Outscraper filters against EXISTING imports in the database
and advise whether they should scrape again or reuse existing data.

Rules:
- Be concise and actionable (plain English, no jargon).
- If exact_matches exist, strongly prefer reusing them to avoid duplicate Outscraper cost.
- If only similar_matches exist, explain overlap and whether a new scrape is likely redundant.
- Never invent import IDs — only reference import_id values from the JSON context.
- Return ONLY valid JSON (no markdown fences) with this schema:
{
  "recommendation": "reuse_existing" | "scrape_again" | "needs_review",
  "headline": "short title under 80 chars",
  "summary": "2-4 sentence overview",
  "reasoning": ["bullet 1", "bullet 2"],
  "suggested_reuse_import_id": null or integer,
  "warnings": ["optional warnings"],
  "confidence": "high" | "medium" | "low"
}
"""


def _friendly_openai_error(response: requests.Response) -> str:
    """Turn OpenAI HTTP errors into actionable messages."""
    try:
        payload = response.json()
        err = payload.get('error') or {}
        message = str(err.get('message') or '').strip()
        code = str(err.get('code') or err.get('type') or '').strip()
    except ValueError:
        message = response.text[:300] if response.text else ''
        code = ''

    if response.status_code == 429:
        if 'quota' in message.lower() or code == 'insufficient_quota':
            return (
                'OpenAI quota exceeded. Add billing/credits at '
                'https://platform.openai.com/settings/organization/billing '
                'or use a key with available balance, then try again.'
            )
        return (
            'OpenAI rate limit (too many requests). Wait 30–60 seconds and click '
            '"Analyze filters with AI" again. Avoid double-clicking the button.'
        )
    if response.status_code == 401:
        detail = message or 'invalid or revoked'
        return (
            f'OpenAI rejected the API key (401): {detail}. '
            'Create a new key at https://platform.openai.com/api-keys, '
            'update OPENAI_API_KEY in `.env`, and restart the dev server.'
        )
    if response.status_code == 403:
        return 'OpenAI access denied (403). Your key may lack chat/completions permission.'
    if message:
        return f'OpenAI error ({response.status_code}): {message}'
    return f'OpenAI error ({response.status_code}): {response.reason}'


def _post_chat_completion(
    api_key: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                OPENAI_CHAT_URL,
                headers={
                    'Authorization': f'Bearer {api_key.strip()}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning('OpenAI request failed: %s', exc)
            raise OpenAIAnalysisError(str(exc)) from exc

        if r.status_code == 429 and attempt < MAX_RETRIES - 1:
            retry_after = r.headers.get('Retry-After')
            try:
                wait = int(retry_after) if retry_after else RETRY_BACKOFF_SECONDS[attempt]
            except ValueError:
                wait = RETRY_BACKOFF_SECONDS[attempt]
            logger.warning('OpenAI 429 — retry %s/%s in %ss', attempt + 1, MAX_RETRIES, wait)
            time.sleep(wait)
            continue

        if not r.ok:
            raise OpenAIAnalysisError(_friendly_openai_error(r))

        try:
            return r.json()
        except ValueError as exc:
            raise OpenAIAnalysisError(f'Invalid JSON from OpenAI: {exc}') from exc

    raise OpenAIAnalysisError(
        'OpenAI rate limit persisted after retries. Wait a minute and try again.'
    )


def analyze_filter_context(
    api_key: str,
    context: dict[str, Any],
    *,
    model: str = 'gpt-4o-mini',
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    if not api_key or not api_key.strip():
        raise OpenAIAnalysisError('OpenAI API key is missing (set OPENAI_API_KEY).')

    user_content = (
        'Analyze this Outscraper filter situation and respond with JSON only:\n\n'
        + json.dumps(context, indent=2, default=str)
    )

    body = _post_chat_completion(
        api_key,
        {
            'model': model,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_content},
            ],
            'temperature': 0.2,
            'response_format': {'type': 'json_object'},
        },
        timeout_seconds=timeout_seconds,
    )

    try:
        content = body['choices'][0]['message']['content']
        parsed = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise OpenAIAnalysisError(f'Unexpected OpenAI response shape: {body}') from exc

    if not isinstance(parsed, dict):
        raise OpenAIAnalysisError('OpenAI did not return a JSON object.')

    return {
        'parsed': parsed,
        'raw_response': body,
        'model': model,
    }
