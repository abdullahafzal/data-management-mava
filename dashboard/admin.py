from django.contrib import admin

from .models import LeadRecord, LeadSourceFile, LeadWorkspace, LeadWorkspaceAction


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
