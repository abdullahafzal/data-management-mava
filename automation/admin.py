from django.contrib import admin

from .models import AutomationRun


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'job_type', 'search_mode', 'status', 'control', 'headless',
        'total_rows', 'rows_done', 'rows_failed', 'created_at',
    ]
    list_filter = ['job_type', 'search_mode', 'status', 'control', 'headless']
    search_fields = ['log', 'error_message']
    readonly_fields = [
        'created_at', 'started_at', 'finished_at', 'log', 'error_message',
        'column_map', 'input_columns',
    ]
