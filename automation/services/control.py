"""Pause / resume / stop signals for long-running automation jobs."""

from __future__ import annotations

import time

from automation.models import AutomationRun


class StopRequested(Exception):
    """User clicked Stop — exit gracefully after last save."""


class RunController:
    def __init__(self, run_id: int, poll_seconds: float = 1.0):
        self.run_id = run_id
        self.poll_seconds = poll_seconds

    def refresh(self) -> AutomationRun:
        return AutomationRun.objects.get(pk=self.run_id)

    def check_stop(self) -> None:
        if self.refresh().control == AutomationRun.Control.STOP:
            raise StopRequested()

    def wait_if_paused(self) -> None:
        while True:
            run = self.refresh()
            if run.control == AutomationRun.Control.STOP:
                raise StopRequested()
            if run.control != AutomationRun.Control.PAUSE:
                return
            if run.status != AutomationRun.Status.PAUSED:
                run.status = AutomationRun.Status.PAUSED
                run.save(update_fields=['status'])
            time.sleep(self.poll_seconds)

    def resume_from_pause(self) -> None:
        run = self.refresh()
        if run.status == AutomationRun.Status.PAUSED:
            run.status = AutomationRun.Status.RUNNING
            run.control = AutomationRun.Control.RUN
            run.save(update_fields=['status', 'control'])

    def update_progress(
        self,
        *,
        total: int | None = None,
        done: int | None = None,
        failed: int | None = None,
        skipped: int | None = None,
        current_row: int | None = None,
        log_line: str | None = None,
    ) -> None:
        run = self.refresh()
        fields: list[str] = []
        if total is not None:
            run.total_rows = total
            fields.append('total_rows')
        if done is not None:
            run.rows_done = done
            run.rows_processed = done
            fields.append('rows_done')
            fields.append('rows_processed')
        if failed is not None:
            run.rows_failed = failed
            fields.append('rows_failed')
        if skipped is not None:
            run.rows_skipped = skipped
            fields.append('rows_skipped')
        if current_row is not None:
            run.current_row_index = current_row
            fields.append('current_row_index')
        if log_line:
            run.log = (run.log or '') + log_line
            if not run.log.endswith('\n'):
                run.log += '\n'
            fields.append('log')
        if fields:
            run.save(update_fields=fields)
