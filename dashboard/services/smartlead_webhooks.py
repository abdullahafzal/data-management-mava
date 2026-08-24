"""Ingest Smartlead EMAIL_SENT / EMAIL_REPLY webhooks into the dashboard DB."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import (
    LeadRecord,
    SmartleadCampaign,
    SmartleadLead,
    SmartleadMessage,
    SmartleadWebhookLog,
)

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = frozenset({'EMAIL_SENT', 'EMAIL_REPLY'})

EVENT_NORMALIZE = {
    'EMAIL_REPLIED': 'EMAIL_REPLY',
}


def _as_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _str(val: Any) -> str:
    if val is None:
        return ''
    return str(val).strip()


def _email_norm(val: Any) -> str:
    return _str(val).lower()


def _dig(payload: dict[str, Any], *paths: str) -> Any:
    """First non-empty value among dotted paths."""
    for path in paths:
        cur: Any = payload
        ok = True
        for part in path.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok and cur is not None and cur != '':
            return cur
    return None


def _parse_dt(val: Any):
    if not val:
        return None
    if isinstance(val, datetime):
        return val if timezone.is_aware(val) else timezone.make_aware(val)
    s = _str(val)
    if not s:
        return None
    dt = parse_datetime(s.replace('Z', '+00:00'))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def normalize_event_type(payload: dict[str, Any]) -> str:
    raw = _str(_dig(payload, 'event_type', 'event')).upper()
    return EVENT_NORMALIZE.get(raw, raw)


def extract_lead_email(payload: dict[str, Any], event_type: str) -> str:
    # Flat docs: to_email is the prospect for SENT; reply may still use to_email as prospect.
    lead_obj = payload.get('lead') if isinstance(payload.get('lead'), dict) else {}
    candidates = [
        lead_obj.get('email'),
        _dig(payload, 'sl_lead_email', 'to_email', 'lead.email'),
    ]
    if event_type == 'EMAIL_REPLY':
        # Prospect is usually from_email on a reply in nested shapes; flat docs keep to_email.
        candidates = [
            lead_obj.get('email'),
            _dig(payload, 'sl_lead_email', 'to_email', 'lead.email', 'from_email'),
        ]
    for c in candidates:
        e = _email_norm(c)
        if e and '@' in e:
            return e
    return ''


def extract_message_id(payload: dict[str, Any], event_type: str) -> str:
    mid = _str(
        _dig(
            payload,
            'message_id',
            'reply_message.message_id',
            'reply.message_id',
            'email.message_id',
            'sent_message.message_id',
        )
    )
    if mid:
        return mid
    # Fallback idempotency key when Smartlead omits message_id (common on EMAIL_REPLY docs).
    parts = [
        event_type,
        _str(_dig(payload, 'campaign_id')),
        extract_lead_email(payload, event_type),
        _str(
            _dig(
                payload,
                'time_sent',
                'time_replied',
                'reply.received_at',
                'timestamp',
            )
        ),
        _str(_dig(payload, 'sequence_number')),
        _str(_dig(payload, 'subject', 'custom_subject', 'reply.subject'))[:80],
    ]
    digest = hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:40]
    return f'generated:{digest}'


def html_to_plain(html: str) -> str:
    text = re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', html or '')
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</p>', '\n', text)
    text = re.sub(r'(?i)</div>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def strip_quoted_reply(text: str) -> str:
    """Keep the new reply text; drop Gmail-style quoted history when possible."""
    if not text:
        return ''
    patterns = [
        r'(?m)^On .+wrote:\s*$',
        r'(?m)^[-_]{2,}\s*Original Message\s*[-_]{2,}\s*$',
        r'(?m)^From:\s.+$',
        r'(?m)^>{1}\s?',
    ]
    cut = len(text)
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m and m.start() > 20:
            cut = min(cut, m.start())
    cleaned = text[:cut].strip()
    return cleaned or text.strip()


def find_lead_record_by_email(email: str) -> LeadRecord | None:
    """Attach to master LeadRecord when the same email already exists."""
    email = _email_norm(email)
    if not email or '@' not in email:
        return None
    # Fast path: search_text contains the email.
    qs = LeadRecord.objects.filter(search_text__icontains=email).order_by('id')[:25]
    for rec in qs:
        data = rec.data or {}
        for val in data.values():
            if isinstance(val, str) and email in val.lower():
                return rec
        vd = rec.verification_data or {}
        emails = vd.get('emails') or {}
        if email in {k.lower() for k in emails.keys()}:
            return rec
    return None


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in (full or '').strip().split() if p]
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], ' '.join(parts[1:])


@transaction.atomic
def process_smartlead_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Persist one Smartlead webhook.
    Returns a small result dict for the HTTP response / logs.
    Always safe to call twice for the same message (idempotent).
    """
    payload = _as_dict(payload)
    event_type = normalize_event_type(payload)
    message_id = extract_message_id(payload, event_type) if event_type else ''
    campaign_id = _str(_dig(payload, 'campaign_id', 'email_campaign_id'))
    lead_sl_id = _str(
        _dig(payload, 'sl_email_lead_id', 'lead_id', 'lead.id')
    )

    log = SmartleadWebhookLog.objects.create(
        event_type=event_type,
        smartlead_message_id=message_id,
        campaign_id=campaign_id,
        lead_id=lead_sl_id,
        payload=payload,
        status=SmartleadWebhookLog.Status.RECEIVED,
    )

    try:
        if event_type not in {'EMAIL_SENT', 'EMAIL_REPLY'}:
            log.status = SmartleadWebhookLog.Status.IGNORED
            log.error = f'Unsupported event_type: {event_type or "(empty)"}'
            log.processed_at = timezone.now()
            log.save(update_fields=['status', 'error', 'processed_at'])
            return {
                'ok': True,
                'status': 'ignored',
                'reason': log.error,
                'log_id': log.id,
            }

        if not campaign_id:
            raise ValueError('Missing campaign_id')

        lead_email = extract_lead_email(payload, event_type)
        if not lead_email:
            raise ValueError('Missing lead email')

        # Duplicate check
        existing = SmartleadMessage.objects.filter(
            smartlead_message_id=message_id
        ).first()
        if existing:
            log.status = SmartleadWebhookLog.Status.DUPLICATE
            log.message = existing
            log.processed_at = timezone.now()
            log.save(update_fields=['status', 'message', 'processed_at'])
            return {
                'ok': True,
                'status': 'duplicate',
                'message_id': existing.id,
                'log_id': log.id,
            }

        campaign, _ = SmartleadCampaign.objects.get_or_create(
            smartlead_campaign_id=campaign_id,
            defaults={
                'name': _str(_dig(payload, 'campaign_name')) or f'Campaign {campaign_id}',
            },
        )
        camp_name = _str(_dig(payload, 'campaign_name'))
        if camp_name and campaign.name != camp_name:
            campaign.name = camp_name
            campaign.save(update_fields=['name', 'updated_at'])

        lead_obj = payload.get('lead') if isinstance(payload.get('lead'), dict) else {}
        first = _str(lead_obj.get('first_name') or _dig(payload, 'first_name'))
        last = _str(lead_obj.get('last_name') or _dig(payload, 'last_name'))
        if not first and not last:
            first, last = _split_name(_str(_dig(payload, 'to_name')))

        lead, created_lead = SmartleadLead.objects.get_or_create(
            campaign=campaign,
            email=lead_email,
            defaults={
                'smartlead_lead_id': lead_sl_id,
                'smartlead_lead_map_id': _str(
                    _dig(payload, 'sl_email_lead_map_id', 'lead_map_id')
                ),
                'first_name': first,
                'last_name': last,
                'lead_record': find_lead_record_by_email(lead_email),
            },
        )
        if not created_lead:
            changed = False
            if lead_sl_id and lead.smartlead_lead_id != lead_sl_id:
                lead.smartlead_lead_id = lead_sl_id
                changed = True
            map_id = _str(_dig(payload, 'sl_email_lead_map_id', 'lead_map_id'))
            if map_id and lead.smartlead_lead_map_id != map_id:
                lead.smartlead_lead_map_id = map_id
                changed = True
            if first and not lead.first_name:
                lead.first_name = first
                changed = True
            if last and not lead.last_name:
                lead.last_name = last
                changed = True
            if lead.lead_record_id is None:
                rec = find_lead_record_by_email(lead_email)
                if rec:
                    lead.lead_record = rec
                    changed = True
            if changed:
                lead.save()

        if event_type == 'EMAIL_SENT':
            direction = SmartleadMessage.Direction.OUTBOUND
            subject = _str(_dig(payload, 'custom_subject', 'subject', 'email.subject'))
            body_html = _str(
                _dig(
                    payload,
                    'custom_email_message',
                    'sent_message.html',
                    'sent_message_body',
                    'sent_message.text',
                    'email.body',
                )
            )
            body_text = html_to_plain(body_html) if '<' in body_html else body_html
            sent_at = _parse_dt(_dig(payload, 'time_sent', 'timestamp'))
            received_at = None
            from_email = _str(_dig(payload, 'from_email', 'sl_senders_mailbox'))
            to_email = lead_email
        else:
            direction = SmartleadMessage.Direction.INBOUND
            subject = _str(
                _dig(payload, 'subject', 'reply.subject', 'reply_message.subject')
            )
            body_html = _str(
                _dig(
                    payload,
                    'reply_body',
                    'reply_message.html',
                    'reply_message.text',
                    'reply.body',
                    'preview_text',
                )
            )
            plain = html_to_plain(body_html) if '<' in body_html else body_html
            body_text = strip_quoted_reply(plain)
            sent_at = None
            received_at = _parse_dt(
                _dig(payload, 'time_replied', 'reply.received_at', 'timestamp')
            )
            # On reply, prospect is from; our mailbox is to (when available).
            from_email = lead_email
            to_email = _str(
                _dig(payload, 'sl_senders_mailbox', 'to_email', 'from_email')
            )

        seq = _dig(payload, 'sequence_number')
        try:
            sequence_number = int(seq) if seq is not None and _str(seq) != '' else None
        except (TypeError, ValueError):
            sequence_number = None

        msg = SmartleadMessage.objects.create(
            lead=lead,
            campaign=campaign,
            smartlead_message_id=message_id,
            event_type=(
                SmartleadMessage.EventType.EMAIL_SENT
                if event_type == 'EMAIL_SENT'
                else SmartleadMessage.EventType.EMAIL_REPLY
            ),
            direction=direction,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            sequence_number=sequence_number,
            sent_at=sent_at,
            received_at=received_at,
            in_reply_to=_str(_dig(payload, 'in_reply_to', 'sent_message.message_id')),
            email_references=_str(_dig(payload, 'references')),
            raw_payload=payload,
            ghl_sync_status=SmartleadMessage.GhlSyncStatus.SKIPPED,
        )

        log.status = SmartleadWebhookLog.Status.PROCESSED
        log.message = msg
        log.processed_at = timezone.now()
        log.save(update_fields=['status', 'message', 'processed_at'])

        return {
            'ok': True,
            'status': 'processed',
            'created_lead': created_lead,
            'message_id': msg.id,
            'lead_id': lead.id,
            'campaign_id': campaign.id,
            'log_id': log.id,
            'event_type': event_type,
            'email': lead_email,
        }
    except Exception as exc:
        logger.exception('Smartlead webhook processing failed')
        log.status = SmartleadWebhookLog.Status.ERROR
        log.error = str(exc)[:2000]
        log.processed_at = timezone.now()
        log.save(update_fields=['status', 'error', 'processed_at'])
        return {
            'ok': False,
            'status': 'error',
            'error': str(exc),
            'log_id': log.id,
        }
