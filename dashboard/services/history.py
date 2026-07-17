"""Campaign action history + undo for Lead DB workspaces."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from ..models import LeadRecord, LeadWorkspace, LeadWorkspaceAction

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
            # Fallback: set all destinations pending
            snap = {
                key: LeadRecord.ProcessStatus.PENDING
                for key, _f, _l in LeadRecord.DESTINATION_FIELDS
            }
        for key, field, _label in LeadRecord.DESTINATION_FIELDS:
            if key in snap:
                setattr(r, field, snap[key])
        r.sync_overall_process_status()
        r.save(update_fields=[
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
