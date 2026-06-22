"""Run automations in a detached subprocess (survives dev-server reloads)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from django.conf import settings

from automation.models import AutomationRun
from automation.services.engine import run_spy_dialer_job
from automation.services.icm_engine import run_icm_job


def execute_spy_dialer_run(run_id: int) -> None:
    run_spy_dialer_job(run_id)


def execute_icm_run(run_id: int) -> None:
    run_icm_job(run_id)


def _manage_py() -> Path:
    return Path(settings.BASE_DIR) / 'manage.py'


def _spawn_worker(command: str, run_id: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(_manage_py()), command, '--job-id', str(run_id)],
        cwd=str(settings.BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def start_spy_dialer_run(run_id: int) -> subprocess.Popen | None:
    """Launch Spy Dialer job in a separate process."""
    run = AutomationRun.objects.get(pk=run_id)
    if not run.column_map:
        raise ValueError('Column mapping is required before starting.')
    if not run.search_mode:
        raise ValueError('Search mode is required before starting.')

    proc = _spawn_worker('run_spy_dialer', run_id)
    run.params = {**(run.params or {}), 'worker_pid': proc.pid}
    run.save(update_fields=['params'])
    return proc


def start_icm_run(run_id: int) -> subprocess.Popen | None:
    """Launch ICM Step 3 job in a separate process."""
    run = AutomationRun.objects.get(pk=run_id)
    if not run.column_map:
        raise ValueError('Column mapping is required before starting.')

    proc = _spawn_worker('run_icm_step3', run_id)
    run.params = {**(run.params or {}), 'worker_pid': proc.pid}
    run.save(update_fields=['params'])
    return proc


def _resume_run(run_id: int, *, start_fn) -> subprocess.Popen | None:
    run = AutomationRun.objects.get(pk=run_id)
    run.control = AutomationRun.Control.RUN

    if run.status == AutomationRun.Status.PAUSED:
        run.status = AutomationRun.Status.RUNNING
        run.save(update_fields=['control', 'status'])
        return None

    if run.status in (
        AutomationRun.Status.STOPPED,
        AutomationRun.Status.FAILED,
        AutomationRun.Status.PENDING,
        AutomationRun.Status.RUNNING,
    ):
        run.status = AutomationRun.Status.PENDING
        run.error_message = ''
        run.save(update_fields=['control', 'status', 'error_message'])
        return start_fn(run_id)

    run.save(update_fields=['control'])
    return start_fn(run_id)


def resume_spy_dialer_run(run_id: int) -> subprocess.Popen | None:
    run = AutomationRun.objects.get(pk=run_id)
    if not run.column_map:
        raise ValueError('Complete column setup before resuming.')
    return _resume_run(run_id, start_fn=start_spy_dialer_run)


def resume_icm_run(run_id: int) -> subprocess.Popen | None:
    run = AutomationRun.objects.get(pk=run_id)
    if not run.column_map:
        raise ValueError('Complete column setup before resuming.')
    return _resume_run(run_id, start_fn=start_icm_run)


def resume_automation_run(run_id: int) -> subprocess.Popen | None:
    run = AutomationRun.objects.get(pk=run_id)
    if run.job_type == AutomationRun.JobType.ICM_PERSONAL:
        return resume_icm_run(run_id)
    return resume_spy_dialer_run(run_id)
