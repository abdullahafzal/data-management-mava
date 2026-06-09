"""Campaign pipeline step status for list / dashboard UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.urls import reverse

from django.conf import settings

from ..models import DataImport, VerificationJob, PhoneVerificationJob


BASE_PIPELINE_STEPS: tuple[dict[str, str], ...] = (
    {'id': 'filters', 'label': 'Filters & AI'},
    {'id': 'upload', 'label': 'Upload'},
    {'id': 'cleaned', 'label': 'Cleaned'},
    {'id': 'millionverifier', 'label': 'MillionVerifier'},
)

SECTION_ANCHORS = {
    'cleaned': 'cleaned-export',
    'millionverifier': 'millionverifier',
    'phone': 'phone-verifier',
    'simpletexting': 'simpletexting',
}


def _xverify_available() -> bool:
    return bool(getattr(settings, 'PHONE_VALIDATION_API_KEY', '')) and bool(
        getattr(settings, 'XVERIFY_DOMAIN', '')
    )


def pipeline_steps(*, xverify_available: bool | None = None) -> tuple[dict[str, str], ...]:
    if xverify_available is None:
        xverify_available = _xverify_available()
    if xverify_available:
        return BASE_PIPELINE_STEPS + ({'id': 'phone', 'label': 'Phone verify'},)
    return BASE_PIPELINE_STEPS + ({'id': 'simpletexting', 'label': 'SimpleTexting'},)


@dataclass
class CampaignProgress:
    is_complete: bool = False
    is_failed: bool = False
    current_step_id: str = 'filters'
    current_step_label: str = 'Filters & AI'
    status_note: str = ''
    open_url: str = ''
    steps: list[dict[str, str]] = field(default_factory=list)


def _latest_import(campaign) -> DataImport | None:
    imports = list(campaign.imports.all())
    return imports[0] if imports else None


def _mv_completed(data_import: DataImport) -> bool:
    cleaned = getattr(data_import, 'cleaned_dataset', None)
    if not cleaned:
        return False
    job = getattr(cleaned, 'verification_job', None)
    return bool(job and job.status == VerificationJob.Status.COMPLETED)


def _phone_completed(data_import: DataImport) -> bool:
    cleaned = getattr(data_import, 'cleaned_dataset', None)
    if not cleaned:
        return False
    job = getattr(cleaned, 'phone_verification_job', None)
    return bool(job and job.status == PhoneVerificationJob.Status.COMPLETED)


def _has_cleaned(data_import: DataImport) -> bool:
    cleaned = getattr(data_import, 'cleaned_dataset', None)
    return bool(cleaned and cleaned.file)


def _step_rows(current_index: int, steps: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i, step in enumerate(steps):
        if i < current_index:
            state = 'done'
        elif i == current_index:
            state = 'current'
        else:
            state = 'pending'
        rows.append({**step, 'state': state})
    return rows


def _open_url(campaign, step_id: str, latest: DataImport | None) -> str:
    if step_id in ('filters', 'upload') or not latest:
        return reverse('pipeline:campaign_detail', kwargs={'pk': campaign.pk})

    if latest.status == DataImport.Status.AWAITING_CONFIRM:
        return reverse('pipeline:upload_confirm', kwargs={'import_pk': latest.pk})

    if latest.status != DataImport.Status.PARSED:
        return reverse('pipeline:campaign_detail', kwargs={'pk': campaign.pk})

    if campaign.is_automatic and _has_cleaned(latest):
        base = reverse('pipeline:automatic_results', kwargs={'import_pk': latest.pk})
    else:
        base = reverse('pipeline:import_detail', kwargs={'import_pk': latest.pk})

    anchor = SECTION_ANCHORS.get(step_id)
    return f'{base}#{anchor}' if anchor else base


def build_campaign_progress(
    campaign,
    *,
    session_analysis: dict[str, Any] | None = None,
) -> CampaignProgress:
    """Derive current pipeline step from imports + optional session (Step 1/2)."""
    steps = pipeline_steps()
    xverify_on = _xverify_available()
    latest = _latest_import(campaign)
    current_index = 0
    status_note = ''
    is_failed = False

    if not latest:
        if session_analysis:
            current_index = 1
            status_note = 'Ready to upload'
        else:
            current_index = 0
            status_note = 'Set filters & run AI check'
    elif latest.status == DataImport.Status.AWAITING_CONFIRM:
        current_index = 1
        status_note = 'Duplicate review'
    elif latest.status == DataImport.Status.FAILED:
        current_index = 1
        is_failed = True
        status_note = 'Upload failed'
    elif latest.status != DataImport.Status.PARSED:
        current_index = 1
        status_note = 'Processing upload'
    elif not _has_cleaned(latest):
        current_index = 2
        status_note = 'Select columns to clean'
    elif not _mv_completed(latest):
        current_index = 3
        status_note = 'Run MillionVerifier'
    elif xverify_on and not _phone_completed(latest):
        current_index = 4
        status_note = 'Run phone verification'
    elif not xverify_on:
        current_index = 4
        status_note = 'Push MillionVerifier good emails to SimpleTexting (testing)'
    else:
        return CampaignProgress(
            is_complete=True,
            current_step_id='complete',
            current_step_label='Complete',
            status_note='Pipeline complete',
            open_url=_open_url(campaign, 'phone', latest),
            steps=[{**step, 'state': 'done'} for step in steps],
        )

    current_step = steps[current_index]
    return CampaignProgress(
        is_complete=False,
        is_failed=is_failed,
        current_step_id=current_step['id'],
        current_step_label=current_step['label'],
        status_note=status_note,
        open_url=_open_url(campaign, current_step['id'], latest),
        steps=_step_rows(current_index, steps),
    )
