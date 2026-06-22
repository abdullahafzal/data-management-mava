"""Detect and repair automation runs stuck without a live worker."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from automation.models import AutomationRun

STALE_RUNNING_SECONDS = 90


def _run_is_configured(run: AutomationRun) -> bool:
    return bool(run.column_map)


def _configure_error_message(run: AutomationRun) -> str:
    if run.job_type == AutomationRun.JobType.ICM_PERSONAL:
        return (
            'This ICM job was never configured. '
            'Open setup, confirm options, then start again.'
        )
    return (
        'This job was never configured (no column mapping). '
        'Open setup, map your columns, then start again.'
    )


def heal_stale_run(run: AutomationRun) -> AutomationRun:
    """Mark orphaned runs failed so the UI stops polling forever."""
    if not _run_is_configured(run):
        if run.status in (
            AutomationRun.Status.RUNNING,
            AutomationRun.Status.PENDING,
            AutomationRun.Status.PAUSED,
        ):
            run.status = AutomationRun.Status.FAILED
            run.error_message = _configure_error_message(run)
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'error_message', 'finished_at'])
        return run

    if (
        run.status == AutomationRun.Status.RUNNING
        and run.total_rows == 0
        and run.started_at
        and timezone.now() - run.started_at > timedelta(seconds=STALE_RUNNING_SECONDS)
    ):
        run.status = AutomationRun.Status.FAILED
        run.error_message = (
            'The background worker stopped (often caused by dev-server reload). '
            'Click Resume to continue from saved progress.'
        )
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])

    return run


def run_is_pollable(run: AutomationRun) -> bool:
    return run.status in (
        AutomationRun.Status.PENDING,
        AutomationRun.Status.RUNNING,
        AutomationRun.Status.PAUSED,
    )
