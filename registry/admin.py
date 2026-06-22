from django.contrib import admin

from .models import NyRegistryDiffRun, NyRegistrySnapshot


@admin.register(NyRegistrySnapshot)
class NyRegistrySnapshotAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'original_filename', 'row_count', 'key_column',
        'is_baseline', 'created_at',
    ]
    list_filter = ['is_baseline', 'file_format']
    search_fields = ['original_filename', 'key_column']


@admin.register(NyRegistryDiffRun)
class NyRegistryDiffRunAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'status', 'is_initial_baseline', 'new_snapshot',
        'baseline_snapshot', 'created_at', 'approved_at',
    ]
    list_filter = ['status', 'is_initial_baseline']
    readonly_fields = ['stats', 'ai_analysis', 'created_at', 'approved_at']
