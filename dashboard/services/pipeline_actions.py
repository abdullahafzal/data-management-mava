"""Run pipeline verification / push steps from Lead DB Proceed."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from django.conf import settings

from pipeline.services.millionverifier_bulk import (
    MillionVerifierBulkError,
    download_report_csv,
    upload_csv,
    wait_until_done,
)
from pipeline.services.simpletexting_api import (
    SimpleTextingError,
    create_contact_on_lists,
    get_or_create_list,
    normalize_phone,
)
from pipeline.services.smartlead_api import SmartleadError, add_leads, create_campaign
from pipeline.services.xverify_api import XVerifyError, verify_phone

from ..models import LeadRecord, LeadWorkspace
from .record_extract import (
    collect_emails,
    collect_emails_for_mv,
    collect_phones,
    collect_phones_for_simpletexting,
    collect_phones_for_xverify,
    normalize_phone_key,
)
from .verification_store import apply_mv_results, apply_xverify_results, parse_mv_report_csv

PIPELINE_DESTINATIONS = frozenset({
    'millionverifier', 'xverify', 'smartlead', 'simpletexting',
})


@dataclass
class DestinationRunResult:
    destination: str
    ok: bool
    message: str
    processed_public_ids: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def get_test_row_limit() -> int:
    limit = int(getattr(settings, 'MILLIONVERIFIER_UPLOAD_ROW_LIMIT', 5) or 0)
    if limit <= 0:
        limit = 5
    return limit


def _millionverifier_configured() -> bool:
    return bool(getattr(settings, 'MILLIONVERIFIER_API_KEY', ''))


def _xverify_configured() -> bool:
    return bool(getattr(settings, 'PHONE_VALIDATION_API_KEY', '')) and bool(
        getattr(settings, 'XVERIFY_DOMAIN', '')
    )


def _smartlead_configured() -> bool:
    return bool(getattr(settings, 'SMARTLEAD_API_KEY', ''))


def _simpletexting_configured() -> bool:
    return bool(getattr(settings, 'SIMPLETEXTING_API_KEY', ''))


def run_millionverifier(
    records: list[LeadRecord],
    *,
    workspace_name: str,
) -> DestinationRunResult:
    if not _millionverifier_configured():
        return DestinationRunResult(
            'millionverifier', False, 'MillionVerifier API key is missing.',
        )
    limit = get_test_row_limit()
    pairs = collect_emails_for_mv(records, limit=limit)
    if not pairs:
        return DestinationRunResult(
            'millionverifier', False,
            'No unverified emails in selected rows (all already checked or no emails).',
        )

    emails = [e for _, e in pairs]
    buf = io.StringIO()
    buf.write('email\n')
    for e in emails:
        buf.write(f'{e}\n')
    csv_bytes = buf.getvalue().encode('utf-8')

    try:
        up = upload_csv(
            settings.MILLIONVERIFIER_API_KEY,
            csv_bytes,
            filename=f'ldb_{workspace_name[:20]}.csv',
        )
        wait_until_done(
            settings.MILLIONVERIFIER_API_KEY,
            up.file_id,
            timeout_seconds=180,
            poll_every_seconds=3.0,
        )
        report_bytes = download_report_csv(
            settings.MILLIONVERIFIER_API_KEY,
            up.file_id,
            filter_name='all',
        )
        email_categories = parse_mv_report_csv(report_bytes)
        updated_ids = apply_mv_results(records, email_categories)
        good_count = sum(1 for c in email_categories.values() if c == 'good')

        return DestinationRunResult(
            'millionverifier',
            True,
            f'MillionVerifier: checked {len(emails)} email(s) (test limit {limit}). '
            f'{good_count} good — statuses updated in table.',
            processed_public_ids=updated_ids,
            details={
                'emails': emails,
                'categories': email_categories,
                'good_count': good_count,
                'file_id': up.file_id,
            },
        )
    except MillionVerifierBulkError as exc:
        return DestinationRunResult('millionverifier', False, f'MillionVerifier failed: {exc}')
    except Exception as exc:
        return DestinationRunResult('millionverifier', False, f'MillionVerifier failed: {exc}')


def _xverifier_process_enabled() -> bool:
    return bool(getattr(settings, 'XVERIFIER_PROCESS', False))


def run_xverify(
    records: list[LeadRecord],
    *,
    workspace_name: str,
) -> DestinationRunResult:
    if not _xverifier_process_enabled():
        return DestinationRunResult(
            'xverify', False,
            'Phone verification is disabled. Set XVERIFIER_PROCESS=true in .env when XVerify is ready.',
        )
    if not _xverify_configured():
        return DestinationRunResult(
            'xverify', False, 'XVerify config missing (PHONE_VALIDATION_API_KEY / XVERIFY_DOMAIN).',
        )
    limit = get_test_row_limit()
    pairs = collect_phones_for_xverify(records, limit=limit)
    if not pairs:
        return DestinationRunResult(
            'xverify', False,
            'No unchecked phone numbers in selected rows (all already verified or no phones).',
        )

    phone_statuses: dict[str, str] = {}
    for _pub_id, phone in pairs:
        row = {'input_phone': phone, 'response': {}, 'error': ''}
        status = 'invalid'
        try:
            res = verify_phone(
                settings.PHONE_VALIDATION_API_KEY,
                settings.XVERIFY_DOMAIN,
                phone,
            )
            row['response'] = res
            status = str(res.get('status') or 'unknown').strip().lower()
        except XVerifyError as exc:
            row['error'] = str(exc)
            status = 'error'
        phone_statuses[normalize_phone_key(phone)] = status

    updated_ids = apply_xverify_results(records, phone_statuses)
    valid_count = sum(1 for s in phone_statuses.values() if s in ('valid', 'ok', 'good'))

    return DestinationRunResult(
        'xverify',
        True,
        f'Phone Verifier (XVerify): checked {len(pairs)} number(s) (test limit {limit}). '
        f'{valid_count} valid — statuses saved.',
        processed_public_ids=updated_ids,
        details={'phone_statuses': phone_statuses, 'valid_count': valid_count},
    )


def run_smartlead(
    records: list[LeadRecord],
    *,
    workspace_name: str,
) -> DestinationRunResult:
    if not _smartlead_configured():
        return DestinationRunResult('smartlead', False, 'Smartlead API key is missing.')
    limit = get_test_row_limit()
    pairs = collect_emails(records, limit=limit, good_only=True)
    if not pairs:
        return DestinationRunResult(
            'smartlead', False,
            'No MillionVerifier-good emails in selected rows. Run MillionVerifier first.',
        )
    public_ids = [p for p, _ in pairs]
    emails = [e for _, e in pairs]
    try:
        camp_resp = create_campaign(settings.SMARTLEAD_API_KEY, workspace_name)
        campaign_id = (
            camp_resp.get('id')
            or camp_resp.get('campaign_id')
            or camp_resp.get('campaignId')
        )
        if not campaign_id:
            raise SmartleadError(f'Could not read campaign id: {camp_resp}')
        leads = [{'email': e} for e in emails]
        add_leads(settings.SMARTLEAD_API_KEY, campaign_id, leads)
        return DestinationRunResult(
            'smartlead',
            True,
            f'Smartlead: pushed {len(leads)} good email(s) to campaign {campaign_id} (test limit {limit}).',
            processed_public_ids=public_ids,
            details={'campaign_id': campaign_id, 'emails': emails},
        )
    except SmartleadError as exc:
        return DestinationRunResult('smartlead', False, f'Smartlead failed: {exc}')
    except Exception as exc:
        return DestinationRunResult('smartlead', False, f'Smartlead failed: {exc}')


def run_simpletexting(
    records: list[LeadRecord],
    *,
    workspace_name: str,
) -> DestinationRunResult:
    if not _xverifier_process_enabled():
        return DestinationRunResult(
            'simpletexting', False,
            'SimpleTexting phone push is disabled. Set XVERIFIER_PROCESS=true in .env when XVerify is ready.',
        )
    if not _simpletexting_configured():
        return DestinationRunResult(
            'simpletexting', False, 'SimpleTexting API key is missing.',
        )
    limit = get_test_row_limit()
    contacts = collect_phones_for_simpletexting(records, limit=limit)
    if not contacts:
        return DestinationRunResult(
            'simpletexting', False,
            'No XVerify-valid phones in selected rows. Run Phone Verifier first.',
        )

    public_ids = [c[0] for c in contacts]
    try:
        list_id, _created = get_or_create_list(
            settings.SIMPLETEXTING_API_KEY,
            workspace_name[:41],
        )
        ok = 0
        for _pub_id, phone, email in contacts:
            create_contact_on_lists(
                settings.SIMPLETEXTING_API_KEY,
                normalize_phone(phone),
                [list_id],
                email=email,
            )
            ok += 1
        return DestinationRunResult(
            'simpletexting',
            True,
            f'SimpleTexting: added {ok} valid phone contact(s) to list "{workspace_name[:41]}" '
            f'(test limit {limit}).',
            processed_public_ids=public_ids,
            details={'list_id': list_id, 'count': ok},
        )
    except SimpleTextingError as exc:
        return DestinationRunResult('simpletexting', False, f'SimpleTexting failed: {exc}')
    except Exception as exc:
        return DestinationRunResult('simpletexting', False, f'SimpleTexting failed: {exc}')


def run_pipeline_destination(
    key: str,
    records: list[LeadRecord],
    *,
    workspace: LeadWorkspace,
) -> DestinationRunResult:
    name = workspace.name
    if key == 'millionverifier':
        return run_millionverifier(records, workspace_name=name)
    if key == 'xverify':
        return run_xverify(records, workspace_name=name)
    if key == 'smartlead':
        return run_smartlead(records, workspace_name=name)
    if key == 'simpletexting':
        return run_simpletexting(records, workspace_name=name)
    return DestinationRunResult(key, False, f'No pipeline action for {key}.')
