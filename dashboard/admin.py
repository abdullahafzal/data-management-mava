from django.contrib import admin

from .models import (
    LeadRecord,
    LeadSourceFile,
    LeadWorkspace,
    LeadWorkspaceAction,
    SmartleadCampaign,
    SmartleadLead,
    SmartleadMessage,
    SmartleadWebhookLog,
)


class LeadSourceFileInline(admin.TabularInline):
    model = LeadSourceFile
    extra = 0
    readonly_fields = ['original_filename', 'source_kind', 'sort_order', 'row_count']


class LeadWorkspaceActionInline(admin.TabularInline):
    model = LeadWorkspaceAction
    extra = 0
    readonly_fields = [
        'action_type', 'summary', 'record_count', 'created_at', 'undone_at',
    ]
    can_delete = False


@admin.register(LeadWorkspace)
class LeadWorkspaceAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'row_count', 'pending_count', 'proceeded_count', 'updated_at',
    ]
    search_fields = ['name']
    readonly_fields = ['columns', 'filter_fields', 'last_merge_report']
    inlines = [LeadSourceFileInline, LeadWorkspaceActionInline]


@admin.register(LeadRecord)
class LeadRecordAdmin(admin.ModelAdmin):
    list_display = ['public_id', 'process_status', 'status', 'is_enriched', 'workspace']
    list_filter = ['process_status', 'status', 'is_enriched']
    search_fields = ['public_id', 'search_text']
    readonly_fields = ['data', 'search_text', 'sources']


@admin.register(LeadWorkspaceAction)
class LeadWorkspaceActionAdmin(admin.ModelAdmin):
    list_display = [
        'workspace', 'action_type', 'record_count', 'created_at', 'undone_at',
    ]
    list_filter = ['action_type']
    search_fields = ['summary', 'workspace__name']
    readonly_fields = [
        'workspace', 'action_type', 'summary', 'record_count',
        'record_ids', 'public_ids', 'meta', 'created_at', 'undone_at', 'reverses',
    ]


@admin.register(SmartleadCampaign)
class SmartleadCampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'smartlead_campaign_id', 'updated_at']
    search_fields = ['name', 'smartlead_campaign_id']


@admin.register(SmartleadLead)
class SmartleadLeadAdmin(admin.ModelAdmin):
    list_display = [
        'email', 'first_name', 'last_name', 'campaign', 'smartlead_lead_id', 'updated_at',
    ]
    search_fields = ['email', 'first_name', 'last_name', 'smartlead_lead_id']
    list_filter = ['campaign']
    raw_id_fields = ['lead_record']


@admin.register(SmartleadMessage)
class SmartleadMessageAdmin(admin.ModelAdmin):
    list_display = [
        'event_type', 'direction', 'subject', 'from_email', 'to_email',
        'campaign', 'sent_at', 'received_at', 'created_at',
    ]
    list_filter = ['event_type', 'direction', 'ghl_sync_status']
    search_fields = [
        'smartlead_message_id', 'subject', 'from_email', 'to_email', 'body_text',
    ]
    readonly_fields = ['raw_payload', 'created_at', 'updated_at']
    raw_id_fields = ['lead', 'campaign']


@admin.register(SmartleadWebhookLog)
class SmartleadWebhookLogAdmin(admin.ModelAdmin):
    list_display = [
        'event_type', 'status', 'smartlead_message_id', 'campaign_id', 'received_at',
    ]
    list_filter = ['status', 'event_type']
    search_fields = ['smartlead_message_id', 'campaign_id', 'lead_id', 'error']
    readonly_fields = ['payload', 'received_at', 'processed_at']
    raw_id_fields = ['message']
