"""Smartlead email history UI — campaigns, leads, chat-style threads."""

from __future__ import annotations

from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render
from django.views import View

from .models import SmartleadCampaign, SmartleadLead, SmartleadMessage


class EmailInboxView(View):
    """List Smartlead campaigns with sent / reply counts."""

    def get(self, request):
        campaigns = (
            SmartleadCampaign.objects.annotate(
                lead_count=Count('leads', distinct=True),
                sent_count=Count(
                    'messages',
                    filter=Q(messages__event_type=SmartleadMessage.EventType.EMAIL_SENT),
                ),
                reply_count=Count(
                    'messages',
                    filter=Q(messages__event_type=SmartleadMessage.EventType.EMAIL_REPLY),
                ),
                last_activity=Max('messages__created_at'),
            )
            .order_by('-last_activity', '-updated_at')
        )
        return render(request, 'dashboard/email_inbox.html', {
            'campaigns': campaigns,
            'total_messages': SmartleadMessage.objects.count(),
            'total_leads': SmartleadLead.objects.count(),
        })


class EmailCampaignView(View):
    """Leads in one campaign — click a person to open the chat thread."""

    def get(self, request, pk):
        campaign = get_object_or_404(SmartleadCampaign, pk=pk)
        q = (request.GET.get('q') or '').strip()
        leads = (
            SmartleadLead.objects.filter(campaign=campaign)
            .annotate(
                msg_count=Count('messages'),
                sent_count=Count(
                    'messages',
                    filter=Q(messages__event_type=SmartleadMessage.EventType.EMAIL_SENT),
                ),
                reply_count=Count(
                    'messages',
                    filter=Q(messages__event_type=SmartleadMessage.EventType.EMAIL_REPLY),
                ),
                last_activity=Max('messages__created_at'),
            )
            .order_by('-last_activity', '-updated_at')
        )
        if q:
            leads = leads.filter(
                Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
            )
        return render(request, 'dashboard/email_campaign.html', {
            'campaign': campaign,
            'leads': leads,
            'q': q,
        })


class EmailConversationView(View):
    """Chat-style thread: outbound (we sent) and inbound (they replied)."""

    def get(self, request, pk):
        lead = get_object_or_404(
            SmartleadLead.objects.select_related('campaign', 'lead_record'),
            pk=pk,
        )
        # Oldest → newest (like WhatsApp). Use coalesced time so replies
        # (received_at only) don't float above sent messages (sent_at only).
        messages_qs = (
            SmartleadMessage.objects.filter(lead=lead)
            .annotate(
                sort_at=Coalesce('sent_at', 'received_at', 'created_at'),
            )
            .order_by('sort_at', 'id')
        )
        siblings = (
            SmartleadLead.objects.filter(campaign=lead.campaign)
            .annotate(last_activity=Max('messages__created_at'))
            .order_by('-last_activity', 'email')[:50]
        )
        return render(request, 'dashboard/email_conversation.html', {
            'lead': lead,
            'campaign': lead.campaign,
            'thread': messages_qs,
            'siblings': siblings,
        })
