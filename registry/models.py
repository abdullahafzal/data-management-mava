from django.db import models


class NyRegistrySnapshot(models.Model):
    """One uploaded NY State business registry export."""

    source_file = models.FileField(upload_to='registry/ny/%Y/%m/')
    original_filename = models.CharField(max_length=255, blank=True)
    file_format = models.CharField(max_length=10, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    columns = models.JSONField(default=list, blank=True)
    key_column = models.CharField(max_length=128)
    status_column = models.CharField(max_length=128, blank=True)
    is_baseline = models.BooleanField(
        default=False,
        help_text='Approved snapshot used for next week comparison.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        label = self.original_filename or f'Snapshot {self.pk}'
        if self.is_baseline:
            return f'{label} (baseline)'
        return label


class NyRegistryDiffRun(models.Model):
    """Weekly comparison: new upload vs last approved baseline."""

    class Status(models.TextChoices):
        PENDING_REVIEW = 'pending_review', 'Pending review'
        APPROVED = 'approved', 'Approved'
        FAILED = 'failed', 'Failed'

    new_snapshot = models.OneToOneField(
        NyRegistrySnapshot,
        on_delete=models.CASCADE,
        related_name='diff_run',
    )
    baseline_snapshot = models.ForeignKey(
        NyRegistrySnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='diff_runs_as_baseline',
    )
    is_initial_baseline = models.BooleanField(default=False)
    stats = models.JSONField(default=dict, blank=True)
    ai_headline = models.CharField(max_length=255, blank=True)
    ai_summary = models.TextField(blank=True)
    ai_analysis = models.JSONField(default=dict, blank=True)
    ai_model = models.CharField(max_length=64, blank=True)
    ai_error = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING_REVIEW,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Diff run {self.pk} — {self.get_status_display()}'
