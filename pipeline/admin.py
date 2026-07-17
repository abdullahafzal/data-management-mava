from django.contrib import admin

from .models import (
    Campaign,
    CleanedDataset,
    DataImport,
    FilterAnalysis,
    ImportSourceFile,
    SavedCategory,
    SavedLocationSuggestion,
    VerificationExport,
    VerificationJob,
)


@admin.register(SavedCategory)
class SavedCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'use_count', 'last_used_at']
    search_fields = ['name', 'name_key']
    ordering = ['-use_count', 'name']


@admin.register(SavedLocationSuggestion)
class SavedLocationSuggestionAdmin(admin.ModelAdmin):
    list_display = ['country', 'label', 'code', 'is_custom', 'use_count', 'last_used_at']
    list_filter = ['country', 'is_custom']
    search_fields = ['label', 'code']
    ordering = ['-use_count', 'label']


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'processing_mode', 'niche', 'location', 'created_at']
    list_filter = ['processing_mode']
    search_fields = ['name', 'niche', 'location']


class VerificationExportInline(admin.TabularInline):
    model = VerificationExport
    extra = 0
    readonly_fields = ['category', 'row_count', 'file']


class ImportSourceFileInline(admin.TabularInline):
    model = ImportSourceFile
    extra = 0
    readonly_fields = ['original_filename', 'sort_order', 'row_count', 'file']


@admin.register(DataImport)
class DataImportAdmin(admin.ModelAdmin):
    list_display = [
        'campaign', 'outscraper_category', 'outscraper_location',
        'row_count', 'status', 'created_at',
    ]
    list_filter = ['status', 'campaign']
    search_fields = [
        'outscraper_category', 'outscraper_location',
        'original_filename', 'filter_fingerprint',
    ]
    readonly_fields = [
        'columns', 'selected_columns', 'row_count', 'merge_report',
        'filter_fingerprint', 'outscraper_services', 'outscraper_advanced', 'extra_tags',
    ]
    inlines = [ImportSourceFileInline]


@admin.register(VerificationJob)
class VerificationJobAdmin(admin.ModelAdmin):
    list_display = ['cleaned_dataset', 'status', 'status_column', 'completed_at']
    inlines = [VerificationExportInline]


@admin.register(FilterAnalysis)
class FilterAnalysisAdmin(admin.ModelAdmin):
    list_display = [
        'data_import', 'recommendation', 'match_type', 'confidence', 'status', 'created_at',
    ]
    list_filter = ['status', 'recommendation', 'match_type']
    search_fields = ['headline', 'summary', 'data_import__outscraper_category']
    readonly_fields = ['context_snapshot', 'reasoning', 'warnings', 'model_name']
