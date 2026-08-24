"""Smartlead webhook HTTP endpoint (Phase 1: persist only)."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .services.smartlead_webhooks import process_smartlead_webhook

logger = logging.getLogger(__name__)


def _webhook_secret_ok(request) -> bool:
    """
    Optional shared secret. If SMARTLEAD_WEBHOOK_SECRET is set in .env, require it via:
      ?secret=...   or   header X-Smartlead-Webhook-Secret: ...
    If the setting is empty, accept all requests (local/ngrok testing).
    """
    expected = (getattr(settings, 'SMARTLEAD_WEBHOOK_SECRET', '') or '').strip()
    if not expected:
        return True
    got = (
        (request.GET.get('secret') or '').strip()
        or (request.headers.get('X-Smartlead-Webhook-Secret') or '').strip()
    )
    return got == expected


@method_decorator(csrf_exempt, name='dispatch')
class SmartleadWebhookView(View):
    """
    POST /api/webhooks/smartlead/

    Smartlead sends EMAIL_SENT / EMAIL_REPLY here.
    We validate, store, and return 200 quickly (Phase 1 — no GHL sync yet).
    """

    http_method_names = ['post', 'get', 'head']

    def get(self, request, *args, **kwargs):
        # Handy for ngrok / health checks in the browser.
        return JsonResponse({
            'ok': True,
            'service': 'smartlead-webhook',
            'accepts': ['EMAIL_SENT', 'EMAIL_REPLY'],
            'hint': 'POST JSON webhook payloads from Smartlead to this URL.',
        })

    def post(self, request, *args, **kwargs):
        if not _webhook_secret_ok(request):
            return JsonResponse({'ok': False, 'error': 'Unauthorized'}, status=401)

        try:
            if request.content_type and 'application/json' in request.content_type:
                payload = json.loads(request.body.decode('utf-8') or '{}')
            else:
                # Form-encoded fallback (rare)
                payload = {k: request.POST.get(k) for k in request.POST.keys()}
                if not payload and request.body:
                    payload = json.loads(request.body.decode('utf-8') or '{}')
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning('Smartlead webhook invalid JSON: %s', exc)
            return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

        if not isinstance(payload, dict):
            return JsonResponse({'ok': False, 'error': 'Payload must be a JSON object'}, status=400)

        result = process_smartlead_webhook(payload)

        # Always 200 once the payload is accepted — Smartlead retries on non-2xx.
        # Business failures are stored in SmartleadWebhookLog (status=error).
        if not result.get('ok'):
            logger.error('Smartlead webhook error: %s', result.get('error'))
        return JsonResponse(result, status=200)
