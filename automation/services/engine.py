"""Spy Dialer batch engine with progress, pause/stop, and checkpoint saves."""

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

from automation.models import AutomationRun
from automation.services.browser import (
    is_driver_alive,
    looks_like_browser_closed,
    recreate_driver,
    result_indicates_browser_closed,
    safe_quit_driver,
)
from automation.services.control import RunController, StopRequested
from automation.services.input_loader import load_mapped_rows


def _backup_output(path: Path) -> Path | None:
    if not path.is_file():
        return None
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = path.with_name(f'{path.stem}_backup_{stamp}{path.suffix}')
    shutil.copy2(path, dest)
    return dest


def run_spy_dialer_job(run_id: int) -> None:
    from automation.spy_dialer_people_automation import (
        PAUSE_BETWEEN_SEARCHES,
        STATUS_DONE,
        STATUS_RETRY,
        _flatten_rows_for_excel,
        _merge_owner_results,
        _normalize_status,
        _save_excel,
        build_driver,
        load_existing_output,
        owner_output_is_done,
        process_one_row,
        process_one_owner_with_network,
    )

    run = AutomationRun.objects.get(pk=run_id)
    controller = RunController(run_id)

    if not run.search_mode:
        run.error_message = 'Search mode not set. Complete the setup step first.'
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save()
        return

    if not run.column_map:
        run.error_message = 'Column mapping not set. Complete the setup step first.'
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save()
        return

    run.status = AutomationRun.Status.RUNNING
    run.control = AutomationRun.Control.RUN
    run.started_at = timezone.now()
    run.error_message = ''
    run.log = (run.log or '') + f'[Job] Worker started at {run.started_at.isoformat()}\n'
    run.save()

    input_path = run.input_file.path
    out_dir = Path(input_path).parent / 'job_outputs'
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f'spy_dialer_run_{run.pk}.xlsx'

    search_mode = run.search_mode or 'people'
    pause = float((run.params or {}).get('pause_between') or PAUSE_BETWEEN_SEARCHES)

    try:
        input_rows = load_mapped_rows(
            input_path,
            search_mode=search_mode,
            column_map=run.column_map or {},
        )
    except Exception as exc:
        run.error_message = str(exc)
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save()
        return

    existing_df = load_existing_output(str(output_path))
    if not existing_df.empty:
        _backup_output(output_path)

    to_search: list[dict] = []
    skipped = 0
    for row in input_rows:
        owner_idx = int(row.get('input_row_index') or 0)
        if existing_df.empty or not owner_output_is_done(existing_df, owner_idx):
            to_search.append(row)
        else:
            skipped += 1

    batch_limit = (run.params or {}).get('batch_limit')
    if batch_limit is not None:
        to_search = to_search[: int(batch_limit)]

    total = skipped + len(to_search)
    batch_note = f' (batch limit {batch_limit})' if batch_limit is not None else ''
    controller.update_progress(
        total=total,
        done=skipped,
        skipped=skipped,
        failed=0,
        log_line=(
            f'[Job] {len(input_rows)} rows in file; {skipped} already done; '
            f'{len(to_search)} to search{batch_note}\n'
        ),
    )

    if not to_search:
        if not existing_df.empty:
            with open(output_path, 'rb') as fh:
                run.output_file.save(output_path.name, File(fh), save=False)
        run.status = AutomationRun.Status.COMPLETED
        run.finished_at = timezone.now()
        run.save()
        return

    if (run.params or {}).get('dry_run'):
        run.status = AutomationRun.Status.COMPLETED
        run.finished_at = timezone.now()
        controller.update_progress(
            log_line=f'[Job] Dry run — would search {len(to_search)} rows\n',
        )
        run.save()
        return

    working_df = existing_df.copy() if not existing_df.empty else pd.DataFrame()
    done = skipped
    failed = 0
    driver = None

    from dataclasses import asdict
    from automation.spy_dialer_people_automation import SpyDialerPeopleRow

    try:
        driver = build_driver(headless=run.headless)
        search_cache: dict[str, list] = {}

        def _log(line: str) -> None:
            controller.update_progress(log_line=line)

        def _ensure_driver() -> None:
            nonlocal driver
            if is_driver_alive(driver):
                return
            safe_quit_driver(driver)
            driver = recreate_driver(
                headless=run.headless,
                build_driver=build_driver,
                log=_log,
            )

        def _execute_search(row: dict) -> list:
            nonlocal driver
            _ensure_driver()
            if search_mode == 'phone':
                return process_one_row(driver, row, search_mode='phone')
            return process_one_owner_with_network(driver, row)

        for n, row in enumerate(to_search, start=1):
            controller.wait_if_paused()
            controller.check_stop()

            owner_idx = int(row.get('input_row_index') or 0)
            label = row.get('search_label') or row.get('owner_name_raw') or owner_idx

            if search_mode == 'phone':
                cache_key = row.get('search_phone') or str(owner_idx)
            else:
                cache_key = '|'.join([
                    (row.get('search_first_name') or '').lower(),
                    (row.get('search_middle') or '').lower(),
                    (row.get('search_last_name') or '').lower(),
                    (row.get('search_city') or '').lower(),
                    (row.get('search_state_abbr') or row.get('search_state') or '').lower(),
                ])

            controller.update_progress(
                current_row=owner_idx,
                log_line=f'[{n}/{len(to_search)}] Searching: {label}\n',
            )

            if cache_key not in search_cache:
                _ensure_driver()
                try:
                    results = _execute_search(row)
                except WebDriverException as exc:
                    if looks_like_browser_closed(exc):
                        safe_quit_driver(driver)
                        driver = recreate_driver(
                            headless=run.headless,
                            build_driver=build_driver,
                            log=_log,
                        )
                        results = _execute_search(row)
                    else:
                        raise

                if result_indicates_browser_closed(results):
                    safe_quit_driver(driver)
                    driver = recreate_driver(
                        headless=run.headless,
                        build_driver=build_driver,
                        log=_log,
                    )
                    results = _execute_search(row)

                search_cache[cache_key] = results
                time.sleep(pause)
            else:
                results = search_cache[cache_key]

            clones: list[SpyDialerPeopleRow] = []
            for scraped in results:
                clone_dict = asdict(scraped)
                clone_dict['input_row_index'] = owner_idx
                clones.append(SpyDialerPeopleRow(**clone_dict))

            working_df = _merge_owner_results(working_df, owner_idx, clones)
            saved = _save_excel(working_df, str(output_path))

            statuses = {_normalize_status(c.status) for c in clones}
            if statuses & STATUS_RETRY or 'error' in statuses or 'timeout' in statuses:
                failed += 1
            elif statuses & STATUS_DONE or 'ok' in statuses:
                done += 1

            controller.update_progress(done=done, failed=failed, skipped=skipped)

            with open(saved, 'rb') as fh:
                run.output_file.save(Path(saved).name, File(fh), save=False)
            run.save(update_fields=['output_file'])

        run.status = AutomationRun.Status.COMPLETED
        run.finished_at = timezone.now()
        run.save()

    except StopRequested:
        if working_df is not None and not working_df.empty:
            saved = _save_excel(working_df, str(output_path))
            with open(saved, 'rb') as fh:
                run.output_file.save(Path(saved).name, File(fh), save=False)
        run.status = AutomationRun.Status.STOPPED
        run.finished_at = timezone.now()
        controller.update_progress(log_line='[Job] Stopped by user — progress saved.\n')
        run.save()

    except Exception as exc:
        if working_df is not None and not working_df.empty:
            try:
                saved = _save_excel(working_df, str(output_path))
                with open(saved, 'rb') as fh:
                    run.output_file.save(Path(saved).name, File(fh), save=False)
            except Exception:
                pass
        run.error_message = str(exc)
        run.status = AutomationRun.Status.FAILED
        run.finished_at = timezone.now()
        controller.update_progress(
            log_line=f'\n[{datetime.now().isoformat()}] ERROR: {exc}\n{traceback.format_exc()}\n',
        )
        run.save()

    finally:
        safe_quit_driver(driver)
