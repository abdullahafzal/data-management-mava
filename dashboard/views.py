from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from urllib.parse import urlencode

from .forms import CreateWorkspaceForm, MergeSourceForm
from .models import LeadRecord, LeadSourceFile, LeadWorkspace, LeadWorkspaceAction
from .services.filters import (
    apply_filters,
    filter_ui_context,
    query_string_from_params,
    workspace_metrics,
)
from .services.history import apply_destination_statuses, log_merge, undo_proceed_action
from .services.ingest import append_source_and_merge, create_workspace_with_master


def _refresh_process_counts(workspace: LeadWorkspace) -> None:
    counts = workspace.records.values('process_status').annotate(n=Count('id'))
    by = {row['process_status']: row['n'] for row in counts}
    workspace.pending_count = by.get(LeadRecord.ProcessStatus.PENDING, 0)
    workspace.proceeded_count = by.get(LeadRecord.ProcessStatus.PROCEEDED, 0)
    workspace.updated_at = timezone.now()
    workspace.save(update_fields=['pending_count', 'proceeded_count', 'updated_at'])


def _redirect_workspace(pk: int, params, *, clear_sel: bool = False) -> redirect:
    url = reverse('dashboard:workspace', kwargs={'pk': pk})
    # Support QueryDict or plain dict
    if hasattr(params, 'keys'):
        data = []
        for key in params.keys():
            if key in (
                'csrfmiddlewaretoken', 'selection_mode', 'record_ids',
                'pending_only', 'clear_sel',
            ) or key.startswith('status_'):
                continue
            if key == 'page':
                continue
            val = params.get(key)
            if val is None:
                continue
            val = str(val).strip()
            if val:
                data.append((key, val))
        if clear_sel:
            data.append(('clear_sel', '1'))
        qs_str = urlencode(data)
    else:
        qs_str = query_string_from_params(params)
        if clear_sel:
            qs_str = f'{qs_str}&clear_sel=1' if qs_str else 'clear_sel=1'
    if qs_str:
        url = f'{url}?{qs_str}'
    return redirect(url)


class DashboardHomeView(View):
    """List workspaces + create new: name + master file → dashboard."""

    def get(self, request):
        return render(request, 'dashboard/home.html', {
            'workspaces': LeadWorkspace.objects.all()[:50],
            'form': CreateWorkspaceForm(),
        })

    def post(self, request):
        form = CreateWorkspaceForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, 'dashboard/home.html', {
                'workspaces': LeadWorkspace.objects.all()[:50],
                'form': form,
            }, status=400)
        try:
            workspace = create_workspace_with_master(
                name=form.cleaned_data['name'],
                uploaded_file=form.cleaned_data['master_file'],
                source_kind=form.cleaned_data['source_kind'],
            )
        except Exception as exc:
            messages.error(request, f'Could not build master database: {exc}')
            return render(request, 'dashboard/home.html', {
                'workspaces': LeadWorkspace.objects.all()[:50],
                'form': form,
            }, status=400)

        messages.success(
            request,
            f'“{workspace.name}” ready — {workspace.row_count:,} master records.',
        )
        return redirect('dashboard:workspace', pk=workspace.pk)


class WorkspaceDashboardView(View):
    def get(self, request, pk):
        workspace = get_object_or_404(LeadWorkspace, pk=pk)
        qs = apply_filters(workspace.records.all(), request.GET, workspace)
        metrics = workspace_metrics(workspace, qs)
        ui = filter_ui_context(workspace, request.GET)

        pending_in_view = qs.filter(
            process_status=LeadRecord.ProcessStatus.PENDING
        ).count()

        paginator = Paginator(qs.order_by('id'), 50)
        page = paginator.get_page(request.GET.get('page') or 1)
        table_columns = workspace.table_columns
        action_history = workspace.actions.all()[:40]

        return render(request, 'dashboard/workspace.html', {
            'workspace': workspace,
            'metrics': metrics,
            'table_columns': table_columns,
            'colspan': len(table_columns) + 8,  # check + ID + cols + Source + 5 dest
            'page': page,
            'records': page.object_list,
            'column_filters': ui['column_filters'],
            'required_filters': ui['required_filters'],
            'additional_filters': ui['additional_filters'],
            'choices': ui,
            'active_filters': ui['active_filters'],
            'filters': ui['selected'],
            'filter_qs': query_string_from_params(request.GET),
            'pending_in_view': pending_in_view,
            'working_set_count': metrics['working_set'],
            'merge_form': MergeSourceForm(),
            'source_kinds': LeadSourceFile.SourceKind,
            'selection_ids_url': reverse('dashboard:selection_ids', kwargs={'pk': pk}),
            'action_history': action_history,
            'action_count': workspace.actions.count(),
            'destinations': LeadRecord.DESTINATION_FIELDS,
            'process_choices': LeadRecord.ProcessStatus.choices,
            'clear_selection': (request.GET.get('clear_sel') or '') == '1',
        })


class WorkspaceMergeView(View):
    def post(self, request, pk):
        workspace = get_object_or_404(LeadWorkspace, pk=pk)
        form = MergeSourceForm(request.POST, request.FILES)
        if not form.is_valid():
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, e)
            return redirect('dashboard:workspace', pk=pk)

        try:
            report = append_source_and_merge(
                workspace,
                form.cleaned_data['source_file'],
                source_kind=form.cleaned_data['source_kind'],
            )
        except Exception as exc:
            messages.error(request, f'Merge failed: {exc}')
            return redirect('dashboard:workspace', pk=pk)

        workspace.refresh_from_db()
        log_merge(
            workspace,
            summary=(
                f"Merged {form.cleaned_data['source_file'].name} "
                f"({form.cleaned_data['source_kind']}) — "
                f"{report.get('formula', '')}"
            ),
            meta={
                'source_kind': form.cleaned_data['source_kind'],
                'filename': form.cleaned_data['source_file'].name,
                'report': report,
            },
        )
        messages.success(
            request,
            f"Merged into master. {report.get('formula', '')} "
            f"— now {workspace.row_count:,} records · "
            f"{workspace.pending_count:,} pending · {workspace.proceeded_count:,} proceeded.",
        )
        return redirect(
            reverse('dashboard:workspace', kwargs={'pk': pk}) + '?clear_sel=1'
        )


class WorkspaceSelectionIdsView(View):
    """Return record IDs matching current filters (for Select filtered results)."""

    def get(self, request, pk):
        workspace = get_object_or_404(LeadWorkspace, pk=pk)
        qs = apply_filters(workspace.records.all(), request.GET, workspace)
        pending_only = (request.GET.get('pending_only') or '').strip() in ('1', 'true', 'yes')
        if pending_only:
            qs = qs.filter(process_status=LeadRecord.ProcessStatus.PENDING)
        # Cap to protect browser memory for huge DBs
        max_ids = 100_000
        ids = list(qs.order_by('id').values_list('id', flat=True)[: max_ids + 1])
        truncated = len(ids) > max_ids
        if truncated:
            ids = ids[:max_ids]
        return JsonResponse({
            'ids': ids,
            'count': len(ids),
            'truncated': truncated,
            'pending_only': pending_only,
        })


class WorkspaceProceedView(View):
    """
    Dummy proceed: set per-destination statuses on selected rows via modal.
    selection_mode=ids|filtered; channel fields status_millionverifier etc.
    Values: pending|proceeded — omit / 'keep' to leave unchanged.
    """

    def post(self, request, pk):
        workspace = get_object_or_404(LeadWorkspace, pk=pk)
        params = request.POST
        mode = (params.get('selection_mode') or '').strip()

        base = workspace.records.all()
        if mode == 'filtered':
            target = apply_filters(base, params, workspace)
            if (params.get('pending_only') or '').strip() in ('1', 'true', 'yes'):
                target = target.filter(process_status=LeadRecord.ProcessStatus.PENDING)
        elif mode == 'ids':
            raw_ids = params.getlist('record_ids')
            try:
                ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
            except (TypeError, ValueError):
                ids = []
            if not ids:
                messages.warning(request, 'No rows selected. Check rows or use “Select filtered results”.')
                return _redirect_workspace(pk, params, clear_sel=True)
            target = base.filter(id__in=ids)
        else:
            messages.warning(
                request,
                'Select rows first (checkboxes) or click “Select filtered results”, then Proceed.',
            )
            return _redirect_workspace(pk, params)

        record_ids = list(target.values_list('id', flat=True))
        if not record_ids:
            messages.warning(request, 'No rows in selection.')
            return _redirect_workspace(pk, params, clear_sel=True)

        channel_updates = {}
        for key, _field, _label in LeadRecord.DESTINATION_FIELDS:
            raw = (params.get(f'status_{key}') or '').strip()
            if raw in ('pending', 'proceeded'):
                channel_updates[key] = raw

        try:
            action = apply_destination_statuses(
                workspace,
                record_ids=record_ids,
                channel_updates=channel_updates,
                selection_mode=mode,
            )
        except ValueError as exc:
            messages.warning(request, str(exc))
            return _redirect_workspace(pk, params)

        messages.success(
            request,
            f'{action.summary}. '
            f'{workspace.pending_count:,} still pending overall. '
            f'Undo available in Action history.',
        )
        return _redirect_workspace(pk, params, clear_sel=True)


class WorkspaceUndoActionView(View):
    """Undo a Proceed action — restore those rows to Pending."""

    def post(self, request, pk, action_pk):
        workspace = get_object_or_404(LeadWorkspace, pk=pk)
        action = get_object_or_404(
            LeadWorkspaceAction, pk=action_pk, workspace=workspace
        )
        try:
            restored = undo_proceed_action(action)
        except ValueError as exc:
            messages.warning(request, str(exc))
            return redirect('dashboard:workspace', pk=pk)

        messages.success(
            request,
            f'Undid action: restored {restored:,} row(s) to prior destination status.',
        )
        return redirect(
            reverse('dashboard:workspace', kwargs={'pk': pk}) + '?clear_sel=1#action-history'
        )
