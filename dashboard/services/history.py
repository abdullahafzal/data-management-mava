"""Campaign action history + undo for Lead DB workspaces."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from ..models import LeadRecord, LeadWorkspace, LeadWorkspaceAction
from .pipeline_actions import (
    PIPELINE_DESTINATIONS,
    DestinationRunResult,
    get_test_row_limit,
    run_pipeline_destination,
)
from .verification_store import snapshot_verification, restore_verification_snapshot

DEST_FIELD_MAP = {
    key: field for key, field, _label in LeadRecord.DESTINATION_FIELDS
}


def _refresh_process_counts(workspace: LeadWorkspace) -> None:
    counts = workspace.records.values('process_status').annotate(n=Count('id'))
    by = {row['process_status']: row['n'] for row in counts}
    workspace.pending_count = by.get(LeadRecord.ProcessStatus.PENDING, 0)
    workspace.proceeded_count = by.get(LeadRecord.ProcessStatus.PROCEEDED, 0)
    workspace.updated_at = timezone.now()
    workspace.save(update_fields=['pending_count', 'proceeded_count', 'updated_at'])


def _overall_from_destinations(statuses: dict[str, str]) -> str:
    vals = list(statuses.values())
    if vals and all(v == LeadRecord.ProcessStatus.PROCEEDED for v in vals):
        return LeadRecord.ProcessStatus.PROCEEDED
    return LeadRecord.ProcessStatus.PENDING


@transaction.atomic
def proceed_workspace(
    workspace: LeadWorkspace,
    *,
    record_ids: list[int],
    channel_updates: dict[str, str],
    selection_mode: str = '',
) -> tuple[LeadWorkspaceAction, list[DestinationRunResult]]:
    """
    Proceed on selected rows: run pipeline APIs where configured, then update statuses.
    Pipeline destinations (millionverifier, xverify, smartlead, simpletexting) call real
    APIs on the first N rows (MILLIONVERIFIER_UPLOAD_ROW_LIMIT, default 5).
    """
    if not record_ids:
        raise ValueError('No rows selected.')
    if not channel_updates:
        raise ValueError('Choose at least one destination status to change.')

    valid = {LeadRecord.ProcessStatus.PENDING, LeadRecord.ProcessStatus.PROCEEDED}
    cleaned = {
        k: v for k, v in channel_updates.items()
        if k in DEST_FIELD_MAP and v in valid
    }
    if not cleaned:
        raise ValueError('Choose at least one destination status to change.')

    rows = list(workspace.records.filter(id__in=record_ids).order_by('id'))
    if not rows:
        raise ValueError('No matching rows found.')

    limit = get_test_row_limit()
    before_snap: dict[str, dict] = {
        r.public_id: snapshot_verification(r) for r in rows
    }
    pipeline_results: list[DestinationRunResult] = []
    pending_updates: dict[str, dict[str, str]] = {
        r.public_id: {} for r in rows
    }

    for key, target in cleaned.items():
        if target == LeadRecord.ProcessStatus.PENDING:
            for r in rows:
                pending_updates[r.public_id][key] = LeadRecord.ProcessStatus.PENDING
            continue

        if key in PIPELINE_DESTINATIONS:
            result = run_pipeline_destination(key, rows, workspace=workspace)
            pipeline_results.append(result)
            if result.ok and key not in ('millionverifier', 'xverify'):
                for pid in result.processed_public_ids:
                    if pid in pending_updates:
                        pending_updates[pid][key] = LeadRecord.ProcessStatus.PROCEEDED
        else:
            capped = rows[:limit]
            for r in capped:
                pending_updates[r.public_id][key] = LeadRecord.ProcessStatus.PROCEEDED

    updated_rows: list[LeadRecord] = []
    for r in rows:
        updates = dict(pending_updates.get(r.public_id) or {})
        if not updates:
            continue
        for key, val in updates.items():
            setattr(r, DEST_FIELD_MAP[key], val)
        r.sync_overall_process_status()
        r.save(update_fields=[
            *[DEST_FIELD_MAP[k] for k in updates],
            'process_status',
            'updated_at',
        ])
        updated_rows.append(r)

    # Reload rows so MV verification_data / gate status are reflected
    affected_ids = {r.id for r in rows}
    rows_after = list(workspace.records.filter(id__in=affected_ids).order_by('id'))
    after_snap = {r.public_id: snapshot_verification(r) for r in rows_after}

    pipeline_updated = any(
        r.destination in ('millionverifier', 'xverify') and r.ok for r in pipeline_results
    )
    if not updated_rows and not pipeline_updated and not any(r.ok for r in pipeline_results):
        errors = [r.message for r in pipeline_results if not r.ok]
        if errors:
            raise ValueError(errors[0])
        raise ValueError('Nothing to update — check selection and destination choices.')

    parts = []
    for key, val in cleaned.items():
        label = next(
            (lab for k, _f, lab in LeadRecord.DESTINATION_FIELDS if k == key),
            key,
        )
        pr = next((r for r in pipeline_results if r.destination == key), None)
        if pr and pr.ok:
            parts.append(f'{label}: {pr.message}')
        elif pr and not pr.ok:
            parts.append(f'{label} FAILED: {pr.message}')
        else:
            n = sum(
                1 for snap in after_snap.values()
                if snap.get(key) == val
            )
            parts.append(f'{label}={val} ({n} row(s))')

    summary = f'Proceed — ' + '; '.join(parts)

    touched = [
        r for r in rows_after
        if before_snap.get(r.public_id) != after_snap.get(r.public_id)
    ]
    action_rows = touched or updated_rows or rows_after

    action = LeadWorkspaceAction.objects.create(
        workspace=workspace,
        action_type=LeadWorkspaceAction.ActionType.PROCEED,
        summary=summary[:512],
        record_count=len(action_rows),
        record_ids=[r.id for r in action_rows][:100_000],
        public_ids=[r.public_id for r in action_rows][:100_000],
        meta={
            'selection_mode': selection_mode,
            'channel_updates': cleaned,
            'before': before_snap,
            'after': after_snap,
            'pipeline_results': [
                {
                    'destination': r.destination,
                    'ok': r.ok,
                    'message': r.message,
                    'processed_public_ids': r.processed_public_ids,
                    'details': r.details,
                }
                for r in pipeline_results
            ],
            'test_limit': limit,
        },
    )
    _refresh_process_counts(workspace)
    return action, pipeline_results


@transaction.atomic
def apply_destination_statuses(
    workspace: LeadWorkspace,
    *,
    record_ids: list[int],
    channel_updates: dict[str, str],
    selection_mode: str = '',
) -> LeadWorkspaceAction:
    """
    Apply per-destination statuses to selected rows.
    channel_updates: {millionverifier: 'proceeded'|'pending', ...} — omit keys to keep.
    Stores before/after snapshots for undo.
    """
    if not record_ids:
        raise ValueError('No rows selected.')
    if not channel_updates:
        raise ValueError('Choose at least one destination status to change.')

    valid = {LeadRecord.ProcessStatus.PENDING, LeadRecord.ProcessStatus.PROCEEDED}
    cleaned = {
        k: v for k, v in channel_updates.items()
        if k in DEST_FIELD_MAP and v in valid
    }
    if not cleaned:
        raise ValueError('Choose at least one destination status to change.')

    qs = workspace.records.filter(id__in=record_ids)
    rows = list(qs)
    if not rows:
        raise ValueError('No matching rows found.')

    before_snap: dict[str, dict] = {}
    after_snap: dict[str, dict] = {}
    for r in rows:
        before = r.destination_statuses()
        before_snap[r.public_id] = dict(before)
        after = dict(before)
        for key, val in cleaned.items():
            after[key] = val
            setattr(r, DEST_FIELD_MAP[key], val)
        r.process_status = _overall_from_destinations(after)
        after_snap[r.public_id] = after
        r.save(update_fields=[
            *[DEST_FIELD_MAP[k] for k in cleaned],
            'process_status',
            'updated_at',
        ])

    labels = [
        label for key, _f, label in LeadRecord.DESTINATION_FIELDS if key in cleaned
    ]
    parts = [f'{label}={cleaned[key]}' for key, _f, label in LeadRecord.DESTINATION_FIELDS if key in cleaned]
    summary = (
        f'Updated {len(rows):,} row(s): ' + ', '.join(parts)
    )

    action = LeadWorkspaceAction.objects.create(
        workspace=workspace,
        action_type=LeadWorkspaceAction.ActionType.PROCEED,
        summary=summary[:512],
        record_count=len(rows),
        record_ids=[r.id for r in rows][:100_000],
        public_ids=[r.public_id for r in rows][:100_000],
        meta={
            'selection_mode': selection_mode,
            'channel_updates': cleaned,
            'before': before_snap,
            'after': after_snap,
            'destinations': labels,
        },
    )
    _refresh_process_counts(workspace)
    return action


@transaction.atomic
def log_merge(
    workspace: LeadWorkspace,
    *,
    summary: str,
    meta: dict | None = None,
) -> LeadWorkspaceAction:
    return LeadWorkspaceAction.objects.create(
        workspace=workspace,
        action_type=LeadWorkspaceAction.ActionType.MERGE,
        summary=summary,
        record_count=workspace.row_count,
        meta=meta or {},
    )


@transaction.atomic
def undo_proceed_action(action: LeadWorkspaceAction) -> int:
    """Restore destination statuses from the action's before snapshot."""
    if not action.can_undo:
        raise ValueError('This action cannot be undone.')

    workspace = action.workspace
    before = (action.meta or {}).get('before') or {}
    public_ids = [p for p in (action.public_ids or []) if p]
    if not public_ids and before:
        public_ids = list(before.keys())

    if not public_ids:
        raise ValueError('No rows stored on this action to undo.')

    rows = list(workspace.records.filter(public_id__in=public_ids))
    if not rows and action.record_ids:
        rows = list(workspace.records.filter(id__in=action.record_ids))

    restored = 0
    for r in rows:
        snap = before.get(r.public_id) or {}
        if not snap:
            snap = snapshot_verification(r)
            for key, _f, _l in LeadRecord.DESTINATION_FIELDS:
                snap[key] = LeadRecord.ProcessStatus.PENDING
            snap['verification_data'] = {}
        restore_verification_snapshot(r, snap)
        r.save(update_fields=[
            'verification_data',
            *[f for _k, f, _l in LeadRecord.DESTINATION_FIELDS],
            'process_status',
            'updated_at',
        ])
        restored += 1

    action.undone_at = timezone.now()
    action.save(update_fields=['undone_at'])

    LeadWorkspaceAction.objects.create(
        workspace=workspace,
        action_type=LeadWorkspaceAction.ActionType.UNDO_PROCEED,
        summary=f'Undid proceed — restored {restored:,} row(s)',
        record_count=restored,
        record_ids=[r.id for r in rows][:100_000],
        public_ids=[r.public_id for r in rows][:100_000],
        reverses=action,
        meta={'reversed_action_id': action.pk},
    )

    _refresh_process_counts(workspace)
    return restored
