from __future__ import annotations

import csv
import io

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from .forms import NyRegistryUploadForm
from .models import NyRegistryDiffRun, NyRegistrySnapshot
from .services.ai_summary import RegistryAIError, analyze_registry_diff
from .services.diff import compare_registry_files
from .services.parser import file_format_label, read_registry_file


def _current_baseline() -> NyRegistrySnapshot | None:
    return (
        NyRegistrySnapshot.objects.filter(is_baseline=True)
        .order_by('-created_at')
        .first()
    )


def _openai_ready() -> bool:
    return bool(getattr(settings, 'OPENAI_API_KEY', ''))


class RegistryDashboardView(View):
    def get(self, request):
        baseline = _current_baseline()
        runs = NyRegistryDiffRun.objects.select_related(
            'new_snapshot', 'baseline_snapshot'
        )[:20]
        return render(request, 'registry/dashboard.html', {
            'baseline': baseline,
            'runs': runs,
            'openai_ready': _openai_ready(),
        })


class RegistryUploadView(View):
    def get(self, request):
        form = NyRegistryUploadForm()
        baseline = _current_baseline()
        return render(request, 'registry/upload.html', {
            'form': form,
            'baseline': baseline,
            'openai_ready': _openai_ready(),
        })

    def post(self, request):
        form = NyRegistryUploadForm(request.POST, request.FILES)
        baseline = _current_baseline()
        if not form.is_valid():
            return render(request, 'registry/upload.html', {
                'form': form,
                'baseline': baseline,
                'openai_ready': _openai_ready(),
            })

        uploaded = form.cleaned_data['source_file']
        key_col = form.cleaned_data['resolved_key_column']
        status_col = form.cleaned_data['resolved_status_column']

        snapshot = NyRegistrySnapshot(
            original_filename=uploaded.name,
            key_column=key_col,
            status_column=status_col,
            columns=form.cleaned_data['columns'],
        )
        snapshot.source_file.save(uploaded.name, uploaded, save=False)
        snapshot.file_format = file_format_label(snapshot.source_file.path)

        try:
            df = read_registry_file(snapshot.source_file.path)
            snapshot.row_count = len(df)
        except Exception as exc:
            messages.error(request, f'Could not read file: {exc}')
            return redirect('registry:upload')

        snapshot.save()

        baseline_path = baseline.source_file.path if baseline else None
        try:
            stats = compare_registry_files(
                new_path=snapshot.source_file.path,
                baseline_path=baseline_path,
                key_column=key_col,
                status_column=status_col or None,
            )
        except Exception as exc:
            snapshot.delete()
            messages.error(request, f'Diff failed: {exc}')
            return redirect('registry:upload')

        diff_run = NyRegistryDiffRun.objects.create(
            new_snapshot=snapshot,
            baseline_snapshot=baseline,
            is_initial_baseline=stats.get('is_initial_baseline', False),
            stats=stats,
        )

        if form.cleaned_data.get('run_ai_summary') and _openai_ready():
            try:
                ai = analyze_registry_diff(settings.OPENAI_API_KEY, stats)
                parsed = ai['parsed']
                diff_run.ai_headline = str(parsed.get('headline') or '')[:255]
                diff_run.ai_summary = str(parsed.get('summary') or '')
                diff_run.ai_analysis = parsed
                diff_run.ai_model = ai.get('model', '')
            except RegistryAIError as exc:
                diff_run.ai_error = str(exc)
                messages.warning(request, f'AI summary skipped: {exc}')
        elif form.cleaned_data.get('run_ai_summary'):
            diff_run.ai_error = 'OpenAI API key is missing.'
            messages.warning(request, 'AI summary skipped — set OPENAI_API_KEY.')

        diff_run.save()

        if stats.get('is_initial_baseline'):
            messages.success(
                request,
                f'First upload saved ({snapshot.row_count} rows). '
                'Review the report and approve as baseline.',
            )
        else:
            messages.success(
                request,
                f'Weekly diff ready: {stats.get("new_count", 0)} new, '
                f'{stats.get("updated_count", 0)} updated, '
                f'{stats.get("removed_count", 0)} removed.',
            )
        return redirect('registry:diff_detail', pk=diff_run.pk)


class RegistryDiffDetailView(View):
    def get(self, request, pk):
        diff_run = get_object_or_404(
            NyRegistryDiffRun.objects.select_related(
                'new_snapshot', 'baseline_snapshot'
            ),
            pk=pk,
        )
        stats = diff_run.stats or {}
        return render(request, 'registry/diff_detail.html', {
            'diff_run': diff_run,
            'stats': stats,
            'openai_ready': _openai_ready(),
        })


class RegistryApproveBaselineView(View):
    def post(self, request, pk):
        diff_run = get_object_or_404(
            NyRegistryDiffRun.objects.select_related('new_snapshot'),
            pk=pk,
        )
        if diff_run.status == NyRegistryDiffRun.Status.APPROVED:
            messages.info(request, 'This snapshot is already the approved baseline.')
            return redirect('registry:diff_detail', pk=pk)

        NyRegistrySnapshot.objects.filter(is_baseline=True).update(is_baseline=False)
        snapshot = diff_run.new_snapshot
        snapshot.is_baseline = True
        snapshot.save(update_fields=['is_baseline'])

        diff_run.status = NyRegistryDiffRun.Status.APPROVED
        diff_run.approved_at = timezone.now()
        diff_run.save(update_fields=['status', 'approved_at'])

        messages.success(
            request,
            f'Approved "{snapshot.original_filename}" as the baseline for next week.',
        )
        return redirect('registry:diff_detail', pk=pk)


class RegistryRerunAIView(View):
    def post(self, request, pk):
        diff_run = get_object_or_404(NyRegistryDiffRun, pk=pk)
        if not _openai_ready():
            messages.error(request, 'OpenAI API key is missing.')
            return redirect('registry:diff_detail', pk=pk)
        try:
            ai = analyze_registry_diff(settings.OPENAI_API_KEY, diff_run.stats or {})
            parsed = ai['parsed']
            diff_run.ai_headline = str(parsed.get('headline') or '')[:255]
            diff_run.ai_summary = str(parsed.get('summary') or '')
            diff_run.ai_analysis = parsed
            diff_run.ai_model = ai.get('model', '')
            diff_run.ai_error = ''
            diff_run.save()
            messages.success(request, 'AI summary updated.')
        except RegistryAIError as exc:
            diff_run.ai_error = str(exc)
            diff_run.save(update_fields=['ai_error'])
            messages.error(request, str(exc))
        return redirect('registry:diff_detail', pk=pk)


class RegistryDownloadChangesView(View):
    """CSV export for new / updated / removed keys from a diff run."""

    def get(self, request, pk, change_type):
        diff_run = get_object_or_404(
            NyRegistryDiffRun.objects.select_related(
                'new_snapshot', 'baseline_snapshot'
            ),
            pk=pk,
        )
        if change_type not in ('new', 'updated', 'removed'):
            return HttpResponse(status=404)

        stats = diff_run.stats or {}
        key_lists = stats.get('key_lists') or {}
        keys = key_lists.get(change_type) or []
        if not keys:
            messages.warning(request, f'No {change_type} records to export.')
            return redirect('registry:diff_detail', pk=pk)

        snap = (
            diff_run.new_snapshot
            if change_type != 'removed'
            else diff_run.baseline_snapshot
        )
        if not snap:
            return HttpResponse(status=404)

        df = read_registry_file(snap.source_file.path)
        key_col = snap.key_column
        df['_registry_key'] = df[key_col].astype(str).str.strip().str.lower()
        subset = df[df['_registry_key'].isin(keys)].drop(columns=['_registry_key'])

        buf = io.StringIO()
        subset.to_csv(buf, index=False, quoting=csv.QUOTE_MINIMAL)
        response = HttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="ny_registry_{change_type}_{diff_run.pk}.csv"'
        )
        return response
