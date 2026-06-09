from django.db import models


class Campaign(models.Model):
    class ProcessingMode(models.TextChoices):
        AUTOMATIC = 'automatic', 'Automatic'
        MANUAL = 'manual', 'Manual'

    name = models.CharField(max_length=255)
    niche = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=512, blank=True)
    notes = models.TextField(blank=True)
    processing_mode = models.CharField(
        max_length=20,
        choices=ProcessingMode.choices,
        default=ProcessingMode.MANUAL,
        help_text='Automatic: fixed columns + one-click processing. Manual: pick columns step-by-step.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def is_automatic(self) -> bool:
        return self.processing_mode == self.ProcessingMode.AUTOMATIC

    @property
    def is_manual(self) -> bool:
        return self.processing_mode == self.ProcessingMode.MANUAL


class SavedCategory(models.Model):
    """User-entered Outscraper categories (autocomplete memory)."""

    name = models.CharField(max_length=255)
    name_key = models.CharField(max_length=255, unique=True, db_index=True)
    use_count = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-use_count', 'name']
        verbose_name_plural = 'saved categories'

    def __str__(self):
        return self.name


class SavedLocationSuggestion(models.Model):
    """Saved regions or custom locations for autocomplete."""

    country = models.CharField(max_length=8, default='US', db_index=True)
    label = models.CharField(max_length=255)
    code = models.CharField(max_length=64, blank=True)
    is_custom = models.BooleanField(default=False)
    use_count = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-use_count', 'label']
        unique_together = [['country', 'label']]
        verbose_name_plural = 'saved location suggestions'

    def __str__(self):
        return f'{self.country} — {self.label}'


class DataImport(models.Model):
    class Status(models.TextChoices):
        UPLOADED = 'uploaded', 'Uploaded'
        AWAITING_CONFIRM = 'awaiting_confirm', 'Awaiting duplicate confirm'
        PARSED = 'parsed', 'Parsed'
        FAILED = 'failed', 'Failed'

    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='imports'
    )
    original_file = models.FileField(upload_to='imports/%Y/%m/')
    original_filename = models.CharField(max_length=255, blank=True)
    file_format = models.CharField(max_length=10, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    columns = models.JSONField(default=list)
    selected_columns = models.JSONField(default=list, blank=True)
    # Outscraper filters (for history + duplicate detection)
    outscraper_category = models.CharField(
        max_length=255, blank=True,
        help_text='Category/query used in Outscraper (e.g. auto body shop)',
    )
    outscraper_location = models.CharField(
        max_length=512, blank=True,
        help_text='Location filter (country + regions or custom text)',
    )
    outscraper_max_results = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Max entries limit set in Outscraper',
    )
    outscraper_services = models.JSONField(
        default=list, blank=True,
        help_text='Enrichment services selected in Outscraper',
    )
    outscraper_advanced = models.JSONField(
        default=dict, blank=True,
        help_text='Advanced Outscraper parameters (quick filters, language, etc.)',
    )
    extra_tags = models.JSONField(
        default=list, blank=True,
        help_text='Additional tags for this scrape',
    )
    filter_fingerprint = models.CharField(
        max_length=64, blank=True, db_index=True,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UPLOADED
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.campaign.name} — {self.created_at:%Y-%m-%d %H:%M}'

    @property
    def filter_summary(self) -> str:
        parts = [self.outscraper_category, self.outscraper_location]
        if self.outscraper_max_results:
            parts.append(f'max {self.outscraper_max_results}')
        return ' · '.join(p for p in parts if p)

    def service_labels(self) -> list[str]:
        from .services.enrichment_services import service_labels
        return service_labels(self.outscraper_services)

    def advanced_display_lines(self) -> list[tuple[str, str]]:
        from .services.advanced_params import display_lines
        return display_lines(self.outscraper_advanced)

    def quick_filter_labels(self) -> list[str]:
        from .services.advanced_params import quick_filter_labels
        advanced = self.outscraper_advanced or {}
        return quick_filter_labels(advanced.get('quick_filters') or [])


class CleanedDataset(models.Model):
    data_import = models.OneToOneField(
        DataImport, on_delete=models.CASCADE, related_name='cleaned_dataset'
    )
    file = models.FileField(upload_to='cleaned/%Y/%m/')
    row_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Cleaned — {self.data_import}'


class VerificationJob(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    cleaned_dataset = models.OneToOneField(
        CleanedDataset, on_delete=models.CASCADE, related_name='verification_job'
    )
    source_file = models.FileField(
        upload_to='verification/source/%Y/%m/', blank=True, null=True
    )
    status_column = models.CharField(max_length=128, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f'Verification — {self.cleaned_dataset}'


class PhoneVerificationJob(models.Model):
    """XVerify phone validation results for one cleaned import."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    cleaned_dataset = models.OneToOneField(
        CleanedDataset, on_delete=models.CASCADE, related_name='phone_verification_job'
    )
    results_file = models.FileField(
        upload_to='phone_verification/%Y/%m/', blank=True, null=True
    )
    total_count = models.PositiveIntegerField(default=0)
    valid_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f'Phone verification — {self.cleaned_dataset}'


class FilterAnalysis(models.Model):
    """OpenAI analysis of Outscraper filter overlap vs database history."""

    class Recommendation(models.TextChoices):
        REUSE = 'reuse_existing', 'Reuse existing data'
        SCRAPE = 'scrape_again', 'Scrape again'
        REVIEW = 'needs_review', 'Needs review'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    data_import = models.ForeignKey(
        DataImport, on_delete=models.CASCADE, related_name='filter_analyses'
    )
    match_type = models.CharField(max_length=16, blank=True)
    recommendation = models.CharField(
        max_length=32, choices=Recommendation.choices, blank=True
    )
    headline = models.CharField(max_length=255, blank=True)
    summary = models.TextField(blank=True)
    reasoning = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    suggested_reuse_import_id = models.PositiveIntegerField(null=True, blank=True)
    confidence = models.CharField(max_length=16, blank=True)
    context_snapshot = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'filter analyses'

    def __str__(self):
        return f'Filter analysis — import {self.data_import_id}'

    @property
    def related_import_ids(self) -> list[int]:
        ctx = self.context_snapshot or {}
        ids: list[int] = []
        seen: set[int] = set()
        for key in ('exact_matches', 'similar_matches'):
            for match in ctx.get(key) or []:
                pk = match.get('import_id')
                if pk is None:
                    continue
                try:
                    pk_int = int(pk)
                except (TypeError, ValueError):
                    continue
                if pk_int not in seen:
                    seen.add(pk_int)
                    ids.append(pk_int)
        return ids

    @property
    def best_import_link_id(self) -> int | None:
        if self.suggested_reuse_import_id:
            return self.suggested_reuse_import_id
        related = self.related_import_ids
        return related[0] if related else None

    @property
    def exact_match_count(self) -> int:
        stats = (self.context_snapshot or {}).get('database_stats') or {}
        return int(stats.get('exact_duplicate_count') or 0)

    @property
    def similar_match_count(self) -> int:
        stats = (self.context_snapshot or {}).get('database_stats') or {}
        return int(stats.get('similar_import_count') or 0)


class VerificationExport(models.Model):
    """One file per MillionVerifier result category (good, risky, unknown, etc.)."""

    job = models.ForeignKey(
        VerificationJob, on_delete=models.CASCADE, related_name='exports'
    )
    category = models.CharField(max_length=64)
    file = models.FileField(upload_to='verification/exports/%Y/%m/')
    row_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category']
        unique_together = [('job', 'category')]

    def __str__(self):
        return f'{self.category} ({self.row_count} rows)'
