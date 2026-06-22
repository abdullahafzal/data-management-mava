from django.db import models


class AutomationRun(models.Model):
    """Tracked browser automation job (Spy Dialer, etc.)."""

    class JobType(models.TextChoices):
        SPY_DIALER = 'spy_dialer', 'Spy Dialer'
        SPY_DIALER_PEOPLE = 'spy_dialer_people', 'Spy Dialer — People'  # legacy
        ICM_PERSONAL = 'icm_personal', 'ICM — Personal contact'

    class SearchMode(models.TextChoices):
        PHONE = 'phone', 'Phone number'
        PEOPLE = 'people', 'Name + city + state'

    class Status(models.TextChoices):
        CONFIGURING = 'configuring', 'Awaiting setup'
        PENDING = 'pending', 'Pending'
        RUNNING = 'running', 'Running'
        PAUSED = 'paused', 'Paused'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        STOPPED = 'stopped', 'Stopped'
        CANCELLED = 'cancelled', 'Cancelled'

    class Control(models.TextChoices):
        RUN = 'run', 'Run'
        PAUSE = 'pause', 'Pause'
        STOP = 'stop', 'Stop'

    job_type = models.CharField(max_length=32, choices=JobType.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.CONFIGURING,
    )
    control = models.CharField(
        max_length=8,
        choices=Control.choices,
        default=Control.RUN,
    )
    search_mode = models.CharField(
        max_length=16,
        choices=SearchMode.choices,
        blank=True,
    )
    column_map = models.JSONField(default=dict, blank=True)
    input_columns = models.JSONField(default=list, blank=True)
    headless = models.BooleanField(
        default=True,
        help_text='Run Chrome without a visible window.',
    )
    input_file = models.FileField(upload_to='automation/inputs/%Y/%m/', blank=True)
    output_file = models.FileField(upload_to='automation/outputs/%Y/%m/', blank=True)
    params = models.JSONField(default=dict, blank=True)
    log = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    rows_done = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    rows_processed = models.PositiveIntegerField(default=0)  # legacy alias = rows_done
    current_row_index = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_job_type_display()} #{self.pk} — {self.get_status_display()}'

    @property
    def rows_remaining(self) -> int:
        if not self.total_rows:
            return 0
        return max(0, self.total_rows - self.rows_done - self.rows_skipped)

    @property
    def progress_percent(self) -> float:
        if not self.total_rows:
            return 0.0
        handled = self.rows_done + self.rows_skipped + self.rows_failed
        return min(100.0, round(100.0 * handled / self.total_rows, 1))
