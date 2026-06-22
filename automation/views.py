import json

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .forms import IcmConfigureForm, IcmUploadForm, SpyDialerConfigureForm, SpyDialerUploadForm
from .models import AutomationRun
from .services.health import heal_stale_run, run_is_pollable
from .services.input_loader import peek_columns
from .services.runner import resume_automation_run, start_icm_run, start_spy_dialer_run


def _configure_url(run: AutomationRun) -> str:
    if run.job_type == AutomationRun.JobType.ICM_PERSONAL:
        return 'automation:icm_configure'
    return 'automation:spy_dialer_configure'


def _run_is_configured(run: AutomationRun) -> bool:
    return bool(run.column_map)


def _icm_column_defaults(columns: list[str]) -> dict:
    """Pre-select common NY registry headers when present."""
    lower = {c.strip().lower(): c for c in columns}
    initial: dict = {'people_name_mode': 'full_name'}

    def pick(*candidates: str) -> str:
        for cand in candidates:
            if cand in lower:
                return lower[cand]
        return ''

    if pick('owner name', 'owner name '):
        initial['full_name'] = pick('owner name', 'owner name ')
    if pick('facility city'):
        initial['city'] = pick('facility city')
    if pick('facility state'):
        initial['state'] = pick('facility state')
    if pick('facility street'):
        initial['address'] = pick('facility street')
    return initial


class AutomationDashboardView(View):
    def get(self, request):
        runs = AutomationRun.objects.all()[:30]
        return render(request, 'automation/dashboard.html', {
            'runs': runs,
        })


class SpyDialerRunView(View):
    """Step 1 — upload file."""

    def get(self, request):
        form = SpyDialerUploadForm()
        return render(request, 'automation/spy_dialer_upload.html', {'form': form})

    def post(self, request):
        form = SpyDialerUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, 'automation/spy_dialer_upload.html', {'form': form})

        uploaded = form.cleaned_data['source_file']
        run = AutomationRun(
            job_type=AutomationRun.JobType.SPY_DIALER,
            status=AutomationRun.Status.CONFIGURING,
        )
        run.input_file.save(uploaded.name, uploaded, save=False)
        run.save()

        try:
            run.input_columns = peek_columns(run.input_file.path)
            run.save(update_fields=['input_columns'])
        except Exception as exc:
            run.delete()
            messages.error(request, f'Could not read file: {exc}')
            return redirect('automation:spy_dialer_run')

        return redirect('automation:spy_dialer_configure', pk=run.pk)


class IcmRunView(View):
    """ICM Step 3 — upload NY registry or master workbook."""

    def get(self, request):
        form = IcmUploadForm()
        return render(request, 'automation/icm_upload.html', {'form': form})

    def post(self, request):
        form = IcmUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, 'automation/icm_upload.html', {'form': form})

        uploaded = form.cleaned_data['source_file']
        run = AutomationRun(
            job_type=AutomationRun.JobType.ICM_PERSONAL,
            status=AutomationRun.Status.CONFIGURING,
        )
        run.input_file.save(uploaded.name, uploaded, save=False)
        run.save()

        try:
            run.input_columns = peek_columns(run.input_file.path)
            run.save(update_fields=['input_columns'])
        except Exception as exc:
            run.delete()
            messages.error(request, f'Could not read file: {exc}')
            return redirect('automation:icm_run')

        return redirect('automation:icm_configure', pk=run.pk)


class SpyDialerConfigureView(View):
    """Step 2 — pick search mode + column mapping, then start."""

    def _needs_configure(self, run: AutomationRun) -> bool:
        if not run.column_map:
            return True
        return run.status in (
            AutomationRun.Status.CONFIGURING,
            AutomationRun.Status.PENDING,
        )

    def get(self, request, pk):
        run = get_object_or_404(AutomationRun, pk=pk)
        heal_stale_run(run)
        run.refresh_from_db()
        if not self._needs_configure(run):
            return redirect('automation:run_detail', pk=pk)

        form = SpyDialerConfigureForm(columns=run.input_columns)
        return render(request, 'automation/spy_dialer_configure.html', {
            'form': form,
            'run': run,
            'columns_json': json.dumps(run.input_columns),
        })

    def post(self, request, pk):
        run = get_object_or_404(AutomationRun, pk=pk)
        form = SpyDialerConfigureForm(request.POST, columns=run.input_columns)
        if not form.is_valid():
            return render(request, 'automation/spy_dialer_configure.html', {
                'form': form,
                'run': run,
                'columns_json': json.dumps(run.input_columns),
            })

        run.search_mode = form.cleaned_data['search_mode']
        run.column_map = form.column_map()
        run.headless = getattr(settings, 'AUTOMATION_HEADLESS_CHROME', True)
        batch = form.cleaned_data.get('batch_limit')
        run.params = {
            **(run.params or {}),
            'batch_limit': batch,
            'pause_between': form.cleaned_data.get('pause_between') or 2.0,
        }
        run.status = AutomationRun.Status.PENDING
        run.control = AutomationRun.Control.RUN
        run.error_message = ''
        run.finished_at = None
        run.save()

        try:
            start_spy_dialer_run(run.pk)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('automation:spy_dialer_configure', pk=run.pk)

        messages.success(request, 'Automation started. Track progress on the run page.')
        return redirect('automation:run_detail', pk=run.pk)


class IcmConfigureView(View):
    """ICM column mapping + batch options, then start."""

    def _needs_configure(self, run: AutomationRun) -> bool:
        if not run.column_map:
            return True
        return run.status in (
            AutomationRun.Status.CONFIGURING,
            AutomationRun.Status.PENDING,
        )

    def get(self, request, pk):
        run = get_object_or_404(AutomationRun, pk=pk, job_type=AutomationRun.JobType.ICM_PERSONAL)
        heal_stale_run(run)
        run.refresh_from_db()
        if not self._needs_configure(run):
            return redirect('automation:run_detail', pk=pk)

        initial = _icm_column_defaults(run.input_columns or [])
        form = IcmConfigureForm(columns=run.input_columns, initial=initial)
        return render(request, 'automation/icm_configure.html', {
            'form': form,
            'run': run,
            'columns_json': json.dumps(run.input_columns),
        })

    def post(self, request, pk):
        run = get_object_or_404(AutomationRun, pk=pk, job_type=AutomationRun.JobType.ICM_PERSONAL)
        form = IcmConfigureForm(request.POST, columns=run.input_columns)
        if not form.is_valid():
            return render(request, 'automation/icm_configure.html', {
                'form': form,
                'run': run,
                'columns_json': json.dumps(run.input_columns),
            })

        batch = form.cleaned_data.get('batch_limit')
        run.column_map = form.column_map()
        run.headless = getattr(settings, 'ICM_HEADLESS', False)
        run.params = {
            **(run.params or {}),
            'batch_limit': batch,
            'force_rebuild_master': form.cleaned_data.get('force_rebuild_master', False),
            'pause_between': form.cleaned_data.get('pause_between') or 2.0,
        }
        run.status = AutomationRun.Status.PENDING
        run.control = AutomationRun.Control.RUN
        run.error_message = ''
        run.finished_at = None
        run.save()

        try:
            start_icm_run(run.pk)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('automation:icm_configure', pk=run.pk)

        messages.success(request, 'ICM automation started. Track progress on the run page.')
        return redirect('automation:run_detail', pk=run.pk)


class AutomationRunDetailView(View):
    def get(self, request, pk):
        run = get_object_or_404(AutomationRun, pk=pk)
        heal_stale_run(run)
        run.refresh_from_db()
        if not _run_is_configured(run) and run.status != AutomationRun.Status.COMPLETED:
            messages.warning(request, 'This job needs setup before it can run.')
            return redirect(_configure_url(run), pk=pk)
        return render(request, 'automation/run_detail.html', {
            'run': run,
            'poll_enabled': run_is_pollable(run),
            'is_icm': run.job_type == AutomationRun.JobType.ICM_PERSONAL,
        })


class AutomationRunStatusView(View):
    """JSON poll endpoint for progress bar — only while job is active."""

    def get(self, request, pk):
        run = get_object_or_404(AutomationRun, pk=pk)
        heal_stale_run(run)
        run.refresh_from_db()
        return JsonResponse({
            'status': run.status,
            'control': run.control,
            'total_rows': run.total_rows,
            'rows_done': run.rows_done,
            'rows_failed': run.rows_failed,
            'rows_skipped': run.rows_skipped,
            'rows_remaining': run.rows_remaining,
            'progress_percent': run.progress_percent,
            'current_row_index': run.current_row_index,
            'has_output': bool(run.output_file),
            'error_message': run.error_message,
            'poll': run_is_pollable(run),
        })


class AutomationRunControlView(View):
    def post(self, request, pk, action):
        run = get_object_or_404(AutomationRun, pk=pk)
        configure_name = _configure_url(run)

        if action == 'pause':
            if run.status == AutomationRun.Status.RUNNING:
                run.control = AutomationRun.Control.PAUSE
                run.save(update_fields=['control'])
                messages.info(request, 'Pause requested — will pause after current row finishes.')
        elif action == 'resume':
            run.control = AutomationRun.Control.RUN
            if run.status == AutomationRun.Status.PAUSED:
                run.status = AutomationRun.Status.RUNNING
                run.save(update_fields=['control', 'status'])
                messages.success(request, 'Resumed.')
            elif run.status in (
                AutomationRun.Status.STOPPED,
                AutomationRun.Status.FAILED,
            ):
                try:
                    resume_automation_run(run.pk)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect(configure_name, pk=run.pk)
                messages.success(request, 'Job restarted from saved progress.')
            else:
                run.save(update_fields=['control'])
        elif action == 'stop':
            run.control = AutomationRun.Control.STOP
            run.save(update_fields=['control'])
            messages.warning(request, 'Stop requested — saving progress after current row.')
        else:
            raise Http404()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'control': run.control, 'status': run.status})
        return redirect('automation:run_detail', pk=pk)


class AutomationDownloadOutputView(View):
    def get(self, request, pk):
        run = get_object_or_404(AutomationRun, pk=pk)
        if not run.output_file:
            raise Http404('No output file for this run.')
        return FileResponse(
            run.output_file.open('rb'),
            as_attachment=True,
            filename=run.output_file.name.rsplit('/', 1)[-1],
        )
