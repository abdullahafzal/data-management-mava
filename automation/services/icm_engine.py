"""ICM Step 3 batch engine — master workbook enrichment with pause/stop and checkpoints."""

from __future__ import annotations

import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
from django.core.files import File
from django.utils import timezone
from selenium.common.exceptions import WebDriverException

from automation.icm import icm_config
from automation.icm import icm_name_search_automation as icm
from automation.icm import ny_data as nd
from automation.icm import ny_step3_icm_personal as step3
from automation.models import AutomationRun
from automation.services.browser import (
    is_driver_alive,
    looks_like_browser_closed,
    recreate_driver,
    result_indicates_browser_closed,
    safe_quit_driver,
)
from automation.services.control import RunController, StopRequested

SESSION_ERROR_HINTS = (
    'login',
    'verification',
    'session',
    'sign in',
    'dashboard search box',
    'profile-picker',
)


def _is_master_workbook(columns: list[str]) -> bool:
    lower = {str(c).strip().lower() for c in columns}
    return 'pipeline_row_id' in lower or 'icm_verified' in lower


def _load_raw_ny_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in ('.xlsx', '.xls'):
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    elif suffix == '.csv':
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    else:
        raise ValueError(f'Unsupported input type: {path.name} (use CSV or Excel).')
    return df.rename(columns=lambda c: str(c).strip())


def _looks_like_session_error(exc: BaseException | None = None, message: str = '') -> bool:
    text = (message or str(exc or '')).lower()
    return any(h in text for h in SESSION_ERROR_HINTS)


def _prepare_master(
    *,
    input_path: Path,
    master_path: Path,
    input_columns: list[str],
    force_rebuild: bool,
    column_map: dict | None = None,
) -> pd.DataFrame:
    master_path.parent.mkdir(parents=True, exist_ok=True)

    if _is_master_workbook(input_columns):
        if force_rebuild or not master_path.is_file():
            shutil.copy2(input_path, master_path)
        return nd.load_master(master_path)

    if force_rebuild or not master_path.is_file():
        df = _load_raw_ny_dataframe(input_path)
        if df.empty:
            raise ValueError('Input file has no data rows.')
        if column_map:
            name_mode = column_map.get('name_mode', 'full_name')
            if name_mode == 'full_name':
                col = column_map.get('full_name')
                if not col or col not in df.columns:
                    raise ValueError(f'Full name column "{col}" not found in file.')
            else:
                for key in ('first_name', 'last_name'):
                    col = column_map.get(key)
                    if not col or col not in df.columns:
                        raise ValueError(f'Column "{col}" not found in file.')
            state_col = column_map.get('state')
            if not state_col or state_col not in df.columns:
                raise ValueError(f'State column "{state_col}" not found in file.')
        else:
            owner_cols = {c.strip().lower() for c in df.columns}
            if 'owner name' not in owner_cols and 'owner name ' not in owner_cols:
                raise ValueError(
                    'NY registry export must include an Owner Name column. '
                    f'Found: {list(df.columns)[:12]}…'
                )
        master = nd.build_master_from_ny(df)
        nd.save_master(master, master_path)
        return master

    return nd.load_master(master_path)


def _pending_rows(
    master: pd.DataFrame,
    batch_limit: int | None,
    column_map: dict | None = None,
) -> tuple[list[dict], int, int]:
    """Return (rows to search, already-done count, corporate/unparseable skipped)."""
    all_icm_rows = step3.master_rows_to_icm_input(master, column_map=column_map)
    todo_df = nd.rows_needing_icm(master)

    want_prids: list[int] = []
    for _, ser in todo_df.iterrows():
        pr = ser.get('pipeline_row_id')
        try:
            want_prids.append(int(float(str(pr).strip())))
        except (ValueError, TypeError):
            pass

    pending = [
        r
        for r in all_icm_rows
        if r.get('owner_type') == 'individual' and r.get('input_row_index') in want_prids
    ]
    if batch_limit is not None:
        pending = pending[: int(batch_limit)]

    ind_total = sum(1 for r in all_icm_rows if r.get('owner_type') == 'individual')
    done_icm = ind_total - len(want_prids)
    skipped = sum(
        1 for r in all_icm_rows if r.get('owner_type') in ('corporate', 'unparseable')
    )
    return pending, done_icm, skipped


def _row_outcome(recs: list) -> str:
    if not recs:
        return 'failed'
    for r in recs:
        if (getattr(r, 'phone_numbers', '') or '').strip():
            return 'done'
        if (getattr(r, 'emails', '') or '').strip():
            return 'done'
        if (getattr(r, 'locations', '') or getattr(r, 'result_card_locations', '') or '').strip():
            return 'done'
        if (getattr(r, 'report_name', '') or getattr(r, 'result_card_name', '') or '').strip():
            return 'done'
    statuses = {(getattr(r, 'status', '') or '').strip().lower() for r in recs}
    if statuses <= {'no_results'}:
        return 'done'
    if statuses & {'timeout', 'error', 'extract_error'}:
        return 'failed'
    return 'done'


def _attach_output(run: AutomationRun, path: Path) -> None:
    with open(path, 'rb') as fh:
        run.output_file.save(path.name, File(fh), save=False)
    run.save(update_fields=['output_file'])


def run_icm_job(run_id: int) -> None:
    icm_config.apply_django_settings()

    run = AutomationRun.objects.get(pk=run_id)
    controller = RunController(run_id)
    params = run.params or {}

    if not run.column_map:
        run.error_message = 'Column mapping not set. Complete the setup step first.'
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save()
        return

    if not run.input_file:
        run.error_message = 'No input file uploaded.'
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save()
        return

    run.status = AutomationRun.Status.RUNNING
    run.control = AutomationRun.Control.RUN
    run.started_at = timezone.now()
    run.error_message = ''
    run.log = (run.log or '') + f'[ICM] Worker started at {run.started_at.isoformat()}\n'
    run.save()

    input_path = Path(run.input_file.path)
    out_dir = input_path.parent / 'job_outputs'
    out_dir.mkdir(parents=True, exist_ok=True)
    master_path = out_dir / f'icm_master_run_{run.pk}.xlsx'
    audit_path = out_dir / f'icm_audit_run_{run.pk}.xlsx'

    nd.configure_run_paths(input_path=input_path, master_path=master_path)

    force_rebuild = bool(params.get('force_rebuild_master'))
    batch_limit = params.get('batch_limit')
    if batch_limit is not None:
        batch_limit = int(batch_limit)
    pause = float(params.get('pause_between') or icm.PAUSE_BETWEEN_SEARCHES)

    master: pd.DataFrame | None = None
    driver = None

    try:
        master = _prepare_master(
            input_path=input_path,
            master_path=master_path,
            input_columns=run.input_columns or [],
            force_rebuild=force_rebuild,
            column_map=run.column_map or None,
        )
    except Exception as exc:
        run.error_message = str(exc)
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        run.log = (run.log or '') + f'[ICM] Setup error: {exc}\n'
        run.save()
        return

    try:
        input_rows, already_done, skipped_other = _pending_rows(
            master, batch_limit, column_map=run.column_map or None,
        )
    except Exception as exc:
        run.error_message = str(exc)
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        run.log = (run.log or '') + f'[ICM] Row prep error: {exc}\n'
        run.save()
        return

    total_batch = len(input_rows)
    controller.update_progress(
        total=total_batch + already_done + skipped_other,
        done=already_done,
        skipped=skipped_other,
        failed=0,
        log_line=(
            f'[ICM] Master: {master_path.name} | '
            f'pending this run: {total_batch} | already verified: {already_done}\n'
        ),
    )

    if not input_rows:
        _attach_output(run, master_path)
        run.status = AutomationRun.Status.COMPLETED
        run.finished_at = timezone.now()
        controller.update_progress(
            log_line='[ICM] Nothing to search — all individuals already have icm_verified.\n',
        )
        run.save()
        return

    done = already_done
    failed = 0
    search_cache: dict[str, list] = {}

    def _log(line: str) -> None:
        controller.update_progress(log_line=line)

    def _build_driver():
        return icm.icm.build_driver(headless=run.headless)

    def _ensure_driver() -> None:
        nonlocal driver
        if is_driver_alive(driver):
            return
        safe_quit_driver(driver)
        driver = recreate_driver(
            headless=run.headless,
            build_driver=_build_driver,
            log=_log,
        )
        try:
            icm.icm.ensure_icm_session(driver)
        except Exception as exc:
            raise RuntimeError(
                f'Could not open Instant Checkmate dashboard: {exc}. '
                'Log in via Chrome profile or set ICM_USE_REMOTE_DEBUGGING=true.'
            ) from exc

    def _run_search(row: dict) -> list:
        nonlocal driver
        _ensure_driver()
        try:
            return icm.process_one_row(driver, row)
        except WebDriverException as exc:
            if looks_like_browser_closed(exc):
                safe_quit_driver(driver)
                driver = recreate_driver(
                    headless=run.headless,
                    build_driver=_build_driver,
                    log=_log,
                )
                icm.icm.ensure_icm_session(driver)
                return icm.process_one_row(driver, row)
            raise

    try:
        driver = _build_driver()
        icm.icm.ensure_icm_session(driver)

        for n, row in enumerate(input_rows, start=1):
            controller.wait_if_paused()
            controller.check_stop()

            prid = row.get('input_row_index')
            label = (
                f"{row.get('search_first_name', '')} {row.get('search_last_name', '')}".strip()
                or row.get('owner_name_raw')
                or prid
            )
            controller.update_progress(
                current_row=int(prid or 0),
                log_line=(
                    f'[{n}/{total_batch}] ICM search: {label} | '
                    f"city={row.get('search_city_typed')} state={row.get('search_state_typed')}\n"
                ),
            )

            cache_key = icm._search_dedupe_key(row)
            if cache_key not in search_cache:
                try:
                    results = _run_search(row)
                except Exception as exc:
                    if _looks_like_session_error(exc):
                        _log('[ICM] Session/login issue — re-opening dashboard and retrying once.\n')
                        safe_quit_driver(driver)
                        driver = recreate_driver(
                            headless=run.headless,
                            build_driver=_build_driver,
                            log=_log,
                        )
                        icm.icm.ensure_icm_session(driver)
                        results = _run_search(row)
                    else:
                        results = [
                            icm.NameSearchResultRow(
                                input_row_index=row.get('input_row_index', 0),
                                owner_name_raw=row.get('owner_name_raw'),
                                search_first_name=row.get('search_first_name'),
                                search_last_name=row.get('search_last_name'),
                                status='error',
                                error=str(exc),
                            )
                        ]

                if result_indicates_browser_closed(results):
                    safe_quit_driver(driver)
                    driver = recreate_driver(
                        headless=run.headless,
                        build_driver=_build_driver,
                        log=_log,
                    )
                    icm.icm.ensure_icm_session(driver)
                    results = _run_search(row)

                search_cache[cache_key] = results
                time.sleep(pause)
            else:
                results = search_cache[cache_key]

            clones = step3._clone_icm_results_for_row(results, row)
            master = step3.apply_icm_result_for_owner(master, row, clones)
            nd.save_master(master, master_path)
            step3._append_audit_rows(audit_path, clones)
            _attach_output(run, master_path)

            if _row_outcome(clones) == 'failed':
                failed += 1
            else:
                done += 1

            controller.update_progress(done=done, failed=failed, skipped=skipped_other)

        run.status = AutomationRun.Status.COMPLETED
        run.finished_at = timezone.now()
        controller.update_progress(log_line='[ICM] Batch completed.\n')
        run.save()

    except StopRequested:
        if master is not None:
            nd.save_master(master, master_path)
            _attach_output(run, master_path)
        run.status = AutomationRun.Status.STOPPED
        run.finished_at = timezone.now()
        controller.update_progress(log_line='[ICM] Stopped by user — master saved.\n')
        run.save()

    except Exception as exc:
        if master is not None:
            try:
                nd.save_master(master, master_path)
                _attach_output(run, master_path)
            except Exception:
                pass
        run.error_message = str(exc)
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        controller.update_progress(
            log_line=f'\n[{datetime.now().isoformat()}] ICM ERROR: {exc}\n{traceback.format_exc()}\n',
        )
        run.save()

    finally:
        safe_quit_driver(driver)
