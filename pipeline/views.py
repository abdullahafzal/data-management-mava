import io
import re
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

import pandas as pd

from .constants import resolve_automatic_columns
from .forms import (
    AddMoreSourceFilesForm,
    CampaignForm,
    ColumnSelectionForm,
    DataImportUploadForm,
    OutscraperFiltersForm,
    VerificationUploadForm,
)
from .models import (
    Campaign,
    CleanedDataset,
    DataImport,
    FilterAnalysis,
    ImportSourceFile,
    PhoneVerificationJob,
    VerificationExport,
    VerificationJob,
)
from .services.automatic import AutomaticPipelineError, run_automatic_pipeline
from .services.multi_merge import merge_files
from .services.enrichment_services import normalize_service_ids
from .services.locations import REGIONS_BY_COUNTRY, parse_location
from .services.suggestions import (
    record_category,
    record_location,
    suggest_categories,
    suggest_locations,
)
from .services.millionverifier_bulk import (
    MillionVerifierBulkError,
    download_report_csv,
    upload_csv,
    wait_until_done,
)
from .services.smartlead_api import SmartleadError, add_leads, create_campaign
from .services.simpletexting_api import (
    SimpleTextingError,
    create_contact_on_lists,
    find_list_details_by_name,
    get_list_details,
    get_or_create_list,
)
from .services.simpletexting_contacts import (
    collect_simpletexting_contacts,
    resolve_simpletexting_source,
)
from .services.ghl_api import GoHighLevelError, upsert_contact
from .services.ghl_contacts import (
    collect_ghl_contacts,
    describe_ghl_sources,
    ghl_push_ready,
)
from .services.xverify_api import XVerifyError, verify_phone
from .services.xverify_results import build_results_csv, good_phones_from_csv_bytes, is_valid_status
from .services.filter_context import build_analysis_context, build_analysis_context_from_filters
from .services.openai_analysis import OpenAIAnalysisError, analyze_filter_context
from .services.cleaner import build_cleaned_csv
from .services.diana import build_diana_handoff_csv
from .services.filters import (
    build_filter_fingerprint,
    find_matching_imports,
    parse_extra_tags,
)
from .services.campaign_progress import build_campaign_progress
from .services.importer import parse_upload, preview_upload
from .services.millionverifier import split_verification_results
from .services.advanced_params import pack_advanced_params
SUGGESTED_COLUMNS = [
    'name',
    'name_for_emails',
    'full_address',
    'street',
    'city',
    'state',
    'postal_code',
    'phone',
    'email_1',
    'email_2',
    'email_3',
    'site',
    'category',
    'query',
]


def _apply_filter_fields(
    data_import: DataImport,
    form: DataImportUploadForm,
    *,
    system_tags: list[str] | None = None,
) -> str:
    data_import.outscraper_category = form.cleaned_data['outscraper_category']
    data_import.outscraper_location = form.cleaned_data['outscraper_location']
    max_results = form.cleaned_data.get('outscraper_max_results')
    data_import.outscraper_max_results = max_results if max_results else None
    data_import.outscraper_services = normalize_service_ids(
        form.cleaned_data.get('outscraper_services') or []
    )
    data_import.outscraper_advanced = pack_advanced_params(form.cleaned_data)
    tags = parse_extra_tags(form.cleaned_data.get('extra_tags', ''))
    if system_tags:
        tags = sorted(set(tags) | set(system_tags))
    data_import.extra_tags = tags
    fingerprint = build_filter_fingerprint(
        data_import.outscraper_category,
        data_import.outscraper_location,
        data_import.outscraper_max_results,
        data_import.outscraper_services,
        data_import.extra_tags,
        advanced=data_import.outscraper_advanced,
    )
    data_import.filter_fingerprint = fingerprint
    return fingerprint


def _remember_filter_suggestions(form: DataImportUploadForm) -> None:
    """Persist categories/locations so they appear in autocomplete next time."""
    for part in (form.cleaned_data.get('outscraper_category') or '').split(','):
        name = part.strip()
        if name:
            record_category(name)

    loc_state = parse_location(form.cleaned_data.get('outscraper_location') or '')
    country = loc_state['country']
    if loc_state['custom'] and loc_state['custom_text']:
        for part in re.split(r'[,;\n]+', loc_state['custom_text']):
            label = part.strip()
            if label:
                record_location(country=country, label=label, code=label, is_custom=True)
    else:
        region_map = dict(REGIONS_BY_COUNTRY.get(country, REGIONS_BY_COUNTRY.get('US', [])))
        for code in loc_state['regions']:
            label = region_map.get(code, code)
            record_location(country=country, label=label, code=code, is_custom=False)


def _sync_campaign_filters(campaign: Campaign, form: DataImportUploadForm) -> None:
    """Keep campaign header in sync with the last Outscraper filters used."""
    campaign.niche = form.cleaned_data['outscraper_category']
    campaign.location = form.cleaned_data['outscraper_location']
    campaign.save(update_fields=['niche', 'location', 'updated_at'])


def _upload_form_initial(campaign: Campaign) -> dict:
    """Pre-fill from campaign defaults when set (optional legacy fields)."""
    initial = {}
    if campaign.niche:
        initial['outscraper_category'] = campaign.niche
    if campaign.location:
        initial['outscraper_location'] = campaign.location
    return initial


def _redirect_after_import(data_import: DataImport):
    """Route to automatic results or manual column picker."""
    if data_import.campaign.is_automatic:
        return redirect('pipeline:automatic_results', import_pk=data_import.pk)
    return redirect('pipeline:select_columns', import_pk=data_import.pk)


def _millionverifier_configured() -> bool:
    return bool(getattr(settings, 'MILLIONVERIFIER_API_KEY', ''))


def _phone_validation_configured() -> bool:
    return bool(getattr(settings, 'PHONE_VALIDATION_API_KEY', ''))

def _smartlead_configured() -> bool:
    return bool(getattr(settings, 'SMARTLEAD_API_KEY', ''))


def _simpletexting_configured() -> bool:
    return bool(getattr(settings, 'SIMPLETEXTING_API_KEY', ''))


def _ghl_configured() -> bool:
    return bool(getattr(settings, 'GHL_API_KEY', '')) and bool(getattr(settings, 'GHL_LOCATION_ID', ''))


def _simpletexting_list_status(campaign_name: str) -> dict | None:
    """Contact list in SimpleTexting (Contacts → Lists), not an SMS Campaign."""
    if not _simpletexting_configured():
        return None
    list_name = (campaign_name or '').strip()[:41]
    if not list_name:
        return None
    try:
        data = find_list_details_by_name(settings.SIMPLETEXTING_API_KEY, list_name)
        if not data:
            return None
        return {
            'list_id': data.get('listId') or data.get('id'),
            'name': data.get('name') or list_name,
            'total_contacts': data.get('totalContactsCount'),
            'active_contacts': data.get('activeContactsCount'),
            'created': data.get('created') or '',
            'updated': data.get('updated') or '',
        }
    except SimpleTextingError:
        return None


def _xverify_configured() -> bool:
    return bool(getattr(settings, 'PHONE_VALIDATION_API_KEY', '')) and bool(getattr(settings, 'XVERIFY_DOMAIN', ''))


def _campaign_analysis_session_key(campaign_pk: int) -> str:
    return f'campaign_{campaign_pk}_filter_analysis'


def _related_import_ids_from_context(context: dict) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for key in ('exact_matches', 'similar_matches'):
        for match in context.get(key) or []:
            pk = match.get('import_id')
            if pk is None:
                continue
            try:
                pk_int = int(pk)
            except (TypeError, ValueError):
                continue
            if pk_int not in seen:
                seen.add(pk_int)
                ids.append(pk_int)
    return ids


def _resolve_suggested_reuse_import_id(parsed: dict, context: dict) -> int | None:
    reuse_id = parsed.get('suggested_reuse_import_id')
    if reuse_id is not None:
        try:
            return int(reuse_id)
        except (TypeError, ValueError):
            pass
    related = _related_import_ids_from_context(context)
    return related[0] if related else None


def _get_campaign_filter_analysis(request, campaign_pk: int) -> dict | None:
    return request.session.get(_campaign_analysis_session_key(campaign_pk))


def _fingerprint_from_filter_form(form: OutscraperFiltersForm) -> str:
    return build_filter_fingerprint(
        form.cleaned_data['outscraper_category'],
        form.cleaned_data['outscraper_location'],
        form.cleaned_data.get('outscraper_max_results'),
        form.cleaned_data.get('outscraper_services') or [],
        parse_extra_tags(form.cleaned_data.get('extra_tags', '')),
        advanced=pack_advanced_params(form.cleaned_data),
    )


def _filter_form_initial_from_session(stored: dict) -> dict:
    return stored.get('form_initial') or {}


def _filter_summary_from_initial(form_initial: dict) -> dict:
    return {
        'category': form_initial.get('outscraper_category', ''),
        'location': form_initial.get('outscraper_location', ''),
        'max_results': form_initial.get('outscraper_max_results'),
    }


def _store_campaign_filter_step(
    request,
    campaign_pk: int,
    *,
    fingerprint: str,
    form_initial: dict,
    status: str,
    parsed: dict | None = None,
    context: dict | None = None,
    error_message: str = '',
) -> None:
    """Unlock Step 2 (upload). status: completed | failed."""
    from django.utils.dateformat import format as date_format
    from django.utils import timezone

    now = timezone.now()
    parsed = parsed or {}
    context = context or {}
    request.session[_campaign_analysis_session_key(campaign_pk)] = {
        'fingerprint': fingerprint,
        'form_initial': form_initial,
        'status': status,
        'error_message': error_message,
        'recommendation': parsed.get('recommendation', ''),
        'headline': parsed.get('headline', ''),
        'summary': parsed.get('summary', ''),
        'reasoning': parsed.get('reasoning') or [],
        'warnings': parsed.get('warnings') or [],
        'confidence': parsed.get('confidence', ''),
        'suggested_reuse_import_id': _resolve_suggested_reuse_import_id(parsed, context),
        'related_import_ids': _related_import_ids_from_context(context),
        'exact_match_count': context.get('database_stats', {}).get('exact_duplicate_count', 0),
        'similar_match_count': context.get('database_stats', {}).get('similar_import_count', 0),
        'match_type': context.get('match_type', ''),
        'analyzed_at': date_format(now, 'M j, Y g:i A'),
        'filter_summary': _filter_summary_from_initial(form_initial),
    }
    request.session.modified = True


def _store_campaign_filter_analysis(
    request,
    campaign_pk: int,
    *,
    fingerprint: str,
    form_initial: dict,
    parsed: dict,
    context: dict,
) -> None:
    _store_campaign_filter_step(
        request,
        campaign_pk,
        fingerprint=fingerprint,
        form_initial=form_initial,
        status='completed',
        parsed=parsed,
        context=context,
    )


def _clear_campaign_filter_analysis(request, campaign_pk: int) -> None:
    key = _campaign_analysis_session_key(campaign_pk)
    if key in request.session:
        del request.session[key]
        request.session.modified = True


def _require_matching_filter_analysis(request, campaign_pk: int, fingerprint: str) -> bool:
    stored = _get_campaign_filter_analysis(request, campaign_pk)
    if not stored:
        return False
    return stored.get('fingerprint') == fingerprint


def _openai_configured() -> bool:
    return bool(getattr(settings, 'OPENAI_API_KEY', ''))


def _latest_filter_analysis(data_import: DataImport) -> FilterAnalysis | None:
    return (
        data_import.filter_analyses.filter(status=FilterAnalysis.Status.COMPLETED)
        .order_by('-created_at')
        .first()
    )


def _filter_analysis_ui_context(data_import: DataImport) -> dict:
    return {
        'filter_analysis': _latest_filter_analysis(data_import),
        'openai_ready': _openai_configured(),
    }


def _duplicate_confirm_context(
    campaign: Campaign,
    data_import: DataImport,
    duplicates: list[DataImport],
    *,
    is_automatic: bool,
    request=None,
) -> dict:
    ctx = {
        'campaign': campaign,
        'data_import': data_import,
        'duplicates': duplicates,
        'is_automatic': is_automatic,
    }
    if request is not None:
        ctx['filter_analysis'] = _get_campaign_filter_analysis(request, campaign.pk)
    return ctx


def run_filter_analysis(data_import: DataImport) -> FilterAnalysis:
    analysis = FilterAnalysis.objects.create(
        data_import=data_import,
        status=FilterAnalysis.Status.PENDING,
    )
    try:
        context = build_analysis_context(data_import)
        result = analyze_filter_context(
            settings.OPENAI_API_KEY,
            context,
            model=getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini'),
        )
        parsed = result['parsed']
        rec = str(parsed.get('recommendation') or '').strip()
        valid_recs = {c.value for c in FilterAnalysis.Recommendation}
        if rec not in valid_recs:
            rec = FilterAnalysis.Recommendation.REVIEW

        reuse_id = _resolve_suggested_reuse_import_id(parsed, context)

        analysis.match_type = context.get('match_type', '')
        analysis.recommendation = rec
        analysis.headline = str(parsed.get('headline') or '')[:255]
        analysis.summary = str(parsed.get('summary') or '')
        analysis.reasoning = parsed.get('reasoning') or []
        analysis.warnings = parsed.get('warnings') or []
        analysis.suggested_reuse_import_id = reuse_id
        analysis.confidence = str(parsed.get('confidence') or '')[:16]
        analysis.context_snapshot = context
        analysis.model_name = result.get('model', '')
        analysis.status = FilterAnalysis.Status.COMPLETED
        analysis.save()
    except Exception as exc:
        analysis.status = FilterAnalysis.Status.FAILED
        analysis.error_message = str(exc)
        analysis.save()
        raise
    return analysis


def _load_import_preview(data_import: DataImport) -> tuple[dict | None, str]:
    if not data_import.original_file:
        return None, ''
    try:
        return preview_upload(data_import.original_file.path), ''
    except Exception as exc:
        return None, str(exc)


def _load_cleaned_preview(cleaned_dataset) -> tuple[dict | None, str]:
    if not cleaned_dataset or not cleaned_dataset.file:
        return None, ''
    try:
        return preview_upload(cleaned_dataset.file.path), ''
    except Exception as exc:
        return None, str(exc)


def _redirect_import_page(
    data_import: DataImport,
    *,
    section: str = '',
) -> HttpResponse:
    """Redirect to import detail or automatic results, optionally scrolled to a section."""
    if data_import.campaign.is_automatic:
        url = reverse('pipeline:automatic_results', kwargs={'import_pk': data_import.pk})
    else:
        url = reverse('pipeline:import_detail', kwargs={'import_pk': data_import.pk})
    if section:
        url = f'{url}#{section}'
    return redirect(url)


def _process_upload_file(data_import: DataImport, uploaded_file=None) -> None:
    if uploaded_file is not None:
        data_import.original_filename = uploaded_file.name
    parsed = parse_upload(data_import.original_file.path)
    data_import.columns = parsed['columns']
    data_import.row_count = parsed['row_count']
    data_import.file_format = parsed['file_format']
    data_import.status = DataImport.Status.PARSED
    data_import.save()


def _save_and_merge_source_files(data_import: DataImport, uploaded_files: list) -> dict:
    """
    Persist each uploaded source file, union-merge into one CSV on original_file.
    First file is the match base. Returns merge_report dict.
    """
    if not uploaded_files:
        raise ValueError('At least one source file is required.')

    # Ensure parent row exists so FK + file storage work.
    if not data_import.pk:
        data_import.save()

    labeled_paths: list[tuple[str, str]] = []
    for i, uploaded in enumerate(uploaded_files):
        name = uploaded.name or f'source_{i + 1}.csv'
        src = ImportSourceFile(
            data_import=data_import,
            original_filename=name,
            sort_order=i,
        )
        src.file.save(name, uploaded, save=False)
        src.save()
        try:
            parsed = parse_upload(src.file.path)
            src.row_count = parsed['row_count']
            src.save(update_fields=['row_count'])
        except Exception:
            pass
        labeled_paths.append((name, src.file.path))

    result = merge_files(labeled_paths)
    merged_name = f'merged_{data_import.pk}.csv'
    if len(uploaded_files) == 1:
        merged_name = uploaded_files[0].name or merged_name
        data_import.original_filename = uploaded_files[0].name or merged_name
    else:
        names = ' + '.join(f.name for f in uploaded_files[:3])
        if len(uploaded_files) > 3:
            names += f' (+{len(uploaded_files) - 3} more)'
        data_import.original_filename = f'Merged ({len(uploaded_files)} files): {names}'[:255]

    data_import.original_file.save(merged_name, ContentFile(result.csv_bytes), save=False)
    data_import.merge_report = result.report
    data_import.file_format = 'csv'
    data_import.save()
    return result.report


def _ensure_import_has_source_files(data_import: DataImport) -> None:
    """Seed ImportSourceFile from original_file when older imports have none."""
    if data_import.source_files.exists():
        return
    if not data_import.original_file:
        raise ValueError('This import has no file to extend.')
    name = data_import.original_filename or Path(data_import.original_file.name).name or 'original.csv'
    src = ImportSourceFile(
        data_import=data_import,
        original_filename=name,
        sort_order=0,
        row_count=data_import.row_count or 0,
    )
    with data_import.original_file.open('rb') as fh:
        src.file.save(name, ContentFile(fh.read()), save=True)


def _rematch_all_source_files(data_import: DataImport) -> dict:
    """Rebuild original_file + merge_report from all ImportSourceFile rows."""
    sources = list(data_import.source_files.order_by('sort_order', 'pk'))
    if not sources:
        raise ValueError('No source files to merge.')
    labeled_paths = [(s.original_filename, s.file.path) for s in sources]
    result = merge_files(labeled_paths)

    n = len(sources)
    if n == 1:
        data_import.original_filename = sources[0].original_filename
        merged_name = sources[0].original_filename or f'merged_{data_import.pk}.csv'
    else:
        names = ' + '.join(s.original_filename for s in sources[:3])
        if n > 3:
            names += f' (+{n - 3} more)'
        data_import.original_filename = f'Merged ({n} files): {names}'[:255]
        merged_name = f'merged_{data_import.pk}.csv'

    data_import.original_file.save(merged_name, ContentFile(result.csv_bytes), save=False)
    data_import.merge_report = result.report
    data_import.file_format = 'csv'
    data_import.save()
    _process_upload_file(data_import)
    return result.report


def _append_source_files_and_rematch(data_import: DataImport, uploaded_files: list) -> dict:
    """Append uploads to this import and re-run union merge. First file stays base."""
    if not uploaded_files:
        raise ValueError('At least one file is required.')
    _ensure_import_has_source_files(data_import)
    max_order = (
        data_import.source_files.order_by('-sort_order')
        .values_list('sort_order', flat=True)
        .first()
    )
    next_order = 0 if max_order is None else max_order + 1

    for i, uploaded in enumerate(uploaded_files):
        name = uploaded.name or f'source_{next_order + i + 1}.csv'
        src = ImportSourceFile(
            data_import=data_import,
            original_filename=name,
            sort_order=next_order + i,
        )
        src.file.save(name, uploaded, save=False)
        src.save()
        try:
            parsed = parse_upload(src.file.path)
            src.row_count = parsed['row_count']
            src.save(update_fields=['row_count'])
        except Exception:
            pass

    return _rematch_all_source_files(data_import)


def _refresh_cleaned_after_merge(data_import: DataImport) -> int | None:
    """Rebuild cleaned export after source merge; returns new row count or None."""
    if data_import.campaign.is_automatic:
        row_count, _, _ = run_automatic_pipeline(data_import)
        return row_count

    columns = data_import.columns or []
    selected = [
        c for c in (data_import.selected_columns or [])
        if c in columns and not str(c).startswith('Unnamed')
    ]
    if not selected:
        # Prefer real identity / contact columns when prior selection was junk-only.
        preferred = [
            'Facility #', 'Facility Name', 'Facility Name Overflow',
            'Facility Street', 'Facility City', 'Facility State',
            'Facility Zip Code', 'Facility County', 'Owner Name',
            'phone', 'email_1', 'email_2', 'email_3',
            'name', 'full_address', 'street', 'city', 'state', 'postal_code',
        ]
        selected = [c for c in preferred if c in columns]
        selected += [
            c for c in columns
            if c not in selected and not str(c).startswith('Unnamed')
            and c not in ('row_sources', 'match_key_used')
        ][:30]
    if not selected:
        return None

    csv_bytes, row_count = build_cleaned_csv(
        data_import.original_file.path,
        selected,
    )
    data_import.selected_columns = selected
    data_import.save(update_fields=['selected_columns'])
    cleaned, _ = CleanedDataset.objects.update_or_create(
        data_import=data_import,
        defaults={'row_count': row_count},
    )
    filename = f'cleaned_{data_import.campaign_id}_{data_import.pk}.csv'
    cleaned.file.save(filename, ContentFile(csv_bytes), save=True)
    return row_count


def _render_campaign_detail_page(
    request,
    campaign: Campaign,
    *,
    filter_form=None,
    upload_form=None,
    upload_unlocked: bool | None = None,
):
    stored = _get_campaign_filter_analysis(request, campaign.pk)
    if upload_unlocked is None:
        upload_unlocked = bool(stored)
    filter_initial = _upload_form_initial(campaign)
    if stored:
        filter_initial = {**filter_initial, **_filter_form_initial_from_session(stored)}
    if filter_form is None:
        filter_form = OutscraperFiltersForm(initial=filter_initial)
    if upload_form is None:
        upload_form = DataImportUploadForm(
            initial=filter_initial,
            automatic=campaign.is_automatic,
        )
    return render(request, 'pipeline/campaign_detail.html', {
        'campaign': campaign,
        'filter_form': filter_form,
        'upload_form': upload_form,
        'filter_analysis': stored,
        'upload_unlocked': upload_unlocked,
        'openai_ready': _openai_configured(),
    })


class CampaignListView(View):
    def get(self, request):
        import_qs = DataImport.objects.select_related(
            'cleaned_dataset__verification_job',
            'cleaned_dataset__phone_verification_job',
        ).order_by('-created_at')
        campaigns = Campaign.objects.prefetch_related(
            Prefetch('imports', queryset=import_qs),
        )
        for campaign in campaigns:
            session = request.session.get(_campaign_analysis_session_key(campaign.pk))
            campaign.pipeline_progress = build_campaign_progress(
                campaign,
                session_analysis=session,
            )
        return render(request, 'pipeline/campaign_list.html', {
            'campaigns': campaigns,
        })


class CampaignCreateView(View):
    def get(self, request):
        return render(request, 'pipeline/campaign_form.html', {
            'form': CampaignForm(),
            'title': 'New campaign',
        })

    def post(self, request):
        form = CampaignForm(request.POST)
        if form.is_valid():
            campaign = form.save()
            messages.success(request, f'Campaign "{campaign.name}" created.')
            return redirect('pipeline:campaign_detail', pk=campaign.pk)
        return render(request, 'pipeline/campaign_form.html', {
            'form': form,
            'title': 'New campaign',
        })


class CampaignDetailView(View):
    def get(self, request, pk):
        campaign = get_object_or_404(
            Campaign.objects.prefetch_related(
                'imports__cleaned_dataset__verification_job__exports'
            ),
            pk=pk,
        )
        stored = _get_campaign_filter_analysis(request, pk)
        upload_unlocked = bool(stored)

        filter_initial = _upload_form_initial(campaign)
        if stored:
            filter_initial = {**filter_initial, **_filter_form_initial_from_session(stored)}

        filter_form = OutscraperFiltersForm(initial=filter_initial)
        upload_form = DataImportUploadForm(
            initial=filter_initial,
            automatic=campaign.is_automatic,
        )

        return _render_campaign_detail_page(
            request,
            campaign,
            filter_form=filter_form,
            upload_form=upload_form,
            upload_unlocked=upload_unlocked,
        )


class CampaignFilterAnalyzeView(View):
    """Step 1: validate filters, run OpenAI, unlock upload."""

    def post(self, request, campaign_pk):
        campaign = get_object_or_404(Campaign, pk=campaign_pk)
        filter_form = OutscraperFiltersForm(request.POST)

        if not filter_form.is_valid():
            messages.error(request, 'Fix filter errors before running AI analysis.')
            return _render_campaign_detail_page(
                request,
                campaign,
                filter_form=filter_form,
                upload_form=DataImportUploadForm(
                    initial=filter_form.data,
                    automatic=campaign.is_automatic,
                ),
                upload_unlocked=False,
            )

        fingerprint = _fingerprint_from_filter_form(filter_form)
        form_initial = {
            k: filter_form.cleaned_data.get(k)
            for k in filter_form.fields
            if k in filter_form.cleaned_data
        }

        if not _openai_configured():
            _store_campaign_filter_step(
                request,
                campaign_pk,
                fingerprint=fingerprint,
                form_initial=form_initial,
                status='failed',
                error_message='OpenAI API key is missing (set OPENAI_API_KEY).',
            )
            messages.warning(
                request,
                'AI analysis is not configured — you can still upload your Outscraper file (Step 2).',
            )
            return redirect('pipeline:campaign_detail', pk=campaign_pk)

        try:
            context = build_analysis_context_from_filters(
                category=filter_form.cleaned_data['outscraper_category'],
                location=filter_form.cleaned_data['outscraper_location'],
                max_results=filter_form.cleaned_data.get('outscraper_max_results'),
                services=filter_form.cleaned_data.get('outscraper_services') or [],
                extra_tags=parse_extra_tags(filter_form.cleaned_data.get('extra_tags', '')),
                advanced=pack_advanced_params(filter_form.cleaned_data),
                campaign_name=campaign.name,
                fingerprint=fingerprint,
            )
            result = analyze_filter_context(
                settings.OPENAI_API_KEY,
                context,
                model=getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini'),
            )
            parsed = result['parsed']
            _store_campaign_filter_analysis(
                request,
                campaign_pk,
                fingerprint=fingerprint,
                form_initial=form_initial,
                parsed=parsed,
                context=context,
            )
            messages.success(
                request,
                'AI analysis complete — you can now upload your Outscraper file (Step 2).',
            )
        except OpenAIAnalysisError as exc:
            _store_campaign_filter_step(
                request,
                campaign_pk,
                fingerprint=fingerprint,
                form_initial=form_initial,
                status='failed',
                error_message=str(exc),
            )
            messages.warning(
                request,
                f'AI analysis failed: {exc} You can still upload your Outscraper file (Step 2).',
            )
        except Exception as exc:
            _store_campaign_filter_step(
                request,
                campaign_pk,
                fingerprint=fingerprint,
                form_initial=form_initial,
                status='failed',
                error_message=str(exc),
            )
            messages.warning(
                request,
                f'AI analysis failed: {exc} You can still upload your Outscraper file (Step 2).',
            )
        else:
            return redirect('pipeline:campaign_detail', pk=campaign_pk)

        return redirect('pipeline:campaign_detail', pk=campaign_pk)


class CampaignFilterResetView(View):
    """Clear analysis and return to Step 1 (edit filters)."""

    def post(self, request, campaign_pk):
        get_object_or_404(Campaign, pk=campaign_pk)
        _clear_campaign_filter_analysis(request, campaign_pk)
        messages.info(request, 'Filters reset — enter filters and run AI analysis again.')
        return redirect('pipeline:campaign_detail', pk=campaign_pk)


class ImportHistoryView(View):
    """All saved Outscraper imports with filter tags (cross-campaign)."""

    def get(self, request):
        qs = DataImport.objects.filter(
            status=DataImport.Status.PARSED,
        ).select_related('campaign')

        cat = request.GET.get('category', '').strip()
        loc = request.GET.get('location', '').strip()
        campaign_id = request.GET.get('campaign', '').strip()
        q_general = request.GET.get('q', '').strip()

        campaign_selected = None
        if campaign_id.isdigit():
            campaign_selected = int(campaign_id)

        if cat:
            qs = qs.filter(outscraper_category__icontains=cat)
        if loc:
            qs = qs.filter(outscraper_location__icontains=loc)
        if campaign_selected is not None:
            qs = qs.filter(campaign_id=campaign_selected)
        if q_general:
            qs = qs.filter(
                Q(outscraper_category__icontains=q_general)
                | Q(outscraper_location__icontains=q_general)
                | Q(campaign__name__icontains=q_general)
                | Q(original_filename__icontains=q_general)
            )

        imports = qs.order_by('-created_at')
        campaigns = Campaign.objects.order_by('name')

        filter_values = {
            'category': cat,
            'location': loc,
            'q': q_general,
        }
        return render(request, 'pipeline/import_history.html', {
            'imports': imports,
            'campaigns': campaigns,
            'filter_values': filter_values,
            'campaign_selected': campaign_selected,
        })


class DataImportUploadView(View):
    def post(self, request, campaign_pk):
        campaign = get_object_or_404(Campaign, pk=campaign_pk)

        if campaign.is_automatic:
            return self._post_automatic(request, campaign)
        return self._post_manual(request, campaign)

    def _block_if_not_analyzed(self, request, campaign, form):
        if not form.is_valid():
            return None
        fingerprint = _fingerprint_from_filter_form(form)
        if _require_matching_filter_analysis(request, campaign.pk, fingerprint):
            return None
        messages.error(
            request,
            'Run AI filter analysis (Step 1) before uploading. '
            'If you changed filters, analyze again.',
        )
        return redirect('pipeline:campaign_detail', pk=campaign.pk)

    def _post_automatic(self, request, campaign):
        form = DataImportUploadForm(
            request.POST, request.FILES, automatic=True,
        )
        if not form.is_valid():
            messages.error(request, 'Please fix the errors below.')
            return _render_campaign_detail_page(request, campaign, upload_form=form)

        blocked = self._block_if_not_analyzed(request, campaign, form)
        if blocked:
            return blocked

        duplicates = find_matching_imports(
            build_filter_fingerprint(
                form.cleaned_data['outscraper_category'],
                form.cleaned_data['outscraper_location'],
                form.cleaned_data.get('outscraper_max_results'),
                form.cleaned_data.get('outscraper_services') or [],
                sorted(
                    set(parse_extra_tags(form.cleaned_data.get('extra_tags', '')))
                    | {'automatic'}
                ),
                advanced=pack_advanced_params(form.cleaned_data),
            )
        )
        confirm = form.cleaned_data.get('confirm_duplicate')
        uploaded_files = form.cleaned_data['source_files']

        data_import = DataImport(campaign=campaign)
        _apply_filter_fields(data_import, form, system_tags=['automatic'])
        data_import.status = DataImport.Status.UPLOADED
        data_import.save()

        try:
            merge_report = _save_and_merge_source_files(data_import, uploaded_files)
        except Exception as exc:
            data_import.status = DataImport.Status.FAILED
            data_import.error_message = str(exc)
            data_import.save()
            messages.error(request, f'Failed to merge files: {exc}')
            return redirect('pipeline:campaign_detail', pk=campaign.pk)

        if duplicates and not confirm:
            data_import.status = DataImport.Status.AWAITING_CONFIRM
            data_import.save(update_fields=['status'])
            return render(request, 'pipeline/upload_confirm_duplicate.html', _duplicate_confirm_context(
                campaign, data_import, duplicates, is_automatic=True, request=request,
            ))

        _sync_campaign_filters(campaign, form)
        _remember_filter_suggestions(form)
        _clear_campaign_filter_analysis(request, campaign.pk)
        return self._finish_automatic_import(
            request, campaign, data_import,
            duplicates=duplicates, confirmed=bool(confirm),
            merge_report=merge_report,
        )

    def _finish_automatic_import(
        self, request, campaign, data_import,
        *, duplicates, confirmed, merge_report=None,
    ):
        data_import.status = DataImport.Status.UPLOADED
        data_import.save(update_fields=['status'])

        try:
            _process_upload_file(data_import)
            row_count, used_cols, missing_cols = run_automatic_pipeline(data_import)
        except AutomaticPipelineError as exc:
            data_import.status = DataImport.Status.FAILED
            data_import.error_message = str(exc)
            data_import.save()
            messages.error(request, str(exc))
            return redirect('pipeline:campaign_detail', pk=campaign.pk)
        except Exception as exc:
            data_import.status = DataImport.Status.FAILED
            data_import.error_message = str(exc)
            data_import.save()
            messages.error(request, f'Failed to process file: {exc}')
            return redirect('pipeline:campaign_detail', pk=campaign.pk)

        if duplicates and confirmed:
            messages.warning(
                request,
                f'Processed anyway — {len(duplicates)} previous import(s) '
                f'matched this campaign filters.',
            )
        else:
            messages.success(
                request,
                f'Automatic processing complete: {row_count} rows, '
                f'{len(used_cols)} columns kept.',
            )
        report = merge_report or data_import.merge_report or {}
        if report.get('files') and len(report['files']) > 1:
            messages.info(
                request,
                f"Multi-file merge: {report.get('formula', '')} "
                f"(matched {report.get('matched_pairs', 0)} row(s)).",
            )
        if missing_cols:
            messages.info(
                request,
                f'{len(missing_cols)} preset column(s) were not in this file '
                f'(skipped).',
            )
        return redirect('pipeline:automatic_results', import_pk=data_import.pk)

    def _post_manual(self, request, campaign):
        form = DataImportUploadForm(request.POST, request.FILES, automatic=False)
        if not form.is_valid():
            messages.error(request, 'Please fix the errors below.')
            return _render_campaign_detail_page(request, campaign, upload_form=form)

        blocked = self._block_if_not_analyzed(request, campaign, form)
        if blocked:
            return blocked

        fingerprint = build_filter_fingerprint(
            form.cleaned_data['outscraper_category'],
            form.cleaned_data['outscraper_location'],
            form.cleaned_data.get('outscraper_max_results'),
            form.cleaned_data.get('outscraper_services') or [],
            parse_extra_tags(form.cleaned_data.get('extra_tags', '')),
            advanced=pack_advanced_params(form.cleaned_data),
        )
        duplicates = find_matching_imports(fingerprint)
        confirm = form.cleaned_data.get('confirm_duplicate')
        uploaded_files = form.cleaned_data['source_files']

        data_import = DataImport(campaign=campaign)
        _apply_filter_fields(data_import, form)
        data_import.status = DataImport.Status.UPLOADED
        data_import.save()

        try:
            merge_report = _save_and_merge_source_files(data_import, uploaded_files)
        except Exception as exc:
            data_import.status = DataImport.Status.FAILED
            data_import.error_message = str(exc)
            data_import.save()
            messages.error(request, f'Failed to merge files: {exc}')
            return redirect('pipeline:campaign_detail', pk=campaign.pk)

        if duplicates and not confirm:
            data_import.status = DataImport.Status.AWAITING_CONFIRM
            data_import.save(update_fields=['status'])
            return render(request, 'pipeline/upload_confirm_duplicate.html', _duplicate_confirm_context(
                campaign, data_import, duplicates, is_automatic=False, request=request,
            ))

        _sync_campaign_filters(campaign, form)
        _remember_filter_suggestions(form)
        _clear_campaign_filter_analysis(request, campaign.pk)

        try:
            _process_upload_file(data_import)
            if duplicates and confirm:
                messages.warning(
                    request,
                    f'Uploaded anyway — {len(duplicates)} previous import(s) '
                    f'used the same Outscraper filters.',
                )
            else:
                messages.success(
                    request,
                    f'Imported {data_import.row_count} rows. Filters saved to history.',
                )
            if merge_report.get('files') and len(merge_report['files']) > 1:
                messages.info(
                    request,
                    f"Multi-file merge: {merge_report.get('formula', '')} "
                    f"(matched {merge_report.get('matched_pairs', 0)} row(s)).",
                )
        except Exception as exc:
            data_import.status = DataImport.Status.FAILED
            data_import.error_message = str(exc)
            data_import.save()
            messages.error(request, f'Failed to parse file: {exc}')
            return redirect('pipeline:campaign_detail', pk=campaign.pk)

        return redirect('pipeline:select_columns', import_pk=data_import.pk)


class DataImportConfirmView(View):
    """Continue or cancel after duplicate-filter warning."""

    def post(self, request, import_pk):
        data_import = get_object_or_404(DataImport, pk=import_pk)
        action = request.POST.get('action')

        if action == 'cancel':
            campaign_id = data_import.campaign_id
            for src in data_import.source_files.all():
                src.file.delete(save=False)
            if data_import.original_file:
                data_import.original_file.delete(save=False)
            data_import.delete()
            messages.info(request, 'Upload cancelled. No new export was added.')
            return redirect('pipeline:campaign_detail', pk=campaign_id)

        if action != 'confirm':
            messages.error(request, 'Invalid action.')
            return redirect('pipeline:upload_confirm', import_pk=import_pk)

        duplicates = find_matching_imports(
            data_import.filter_fingerprint, exclude_pk=data_import.pk
        )
        campaign = data_import.campaign
        try:
            _process_upload_file(data_import, None)
            if campaign.is_automatic:
                run_automatic_pipeline(data_import)
                messages.warning(
                    request,
                    f'Automatic processing done — {len(duplicates)} previous '
                    f'import(s) used the same filters.',
                )
                return redirect('pipeline:automatic_results', import_pk=data_import.pk)
            messages.warning(
                request,
                f'New file saved — {len(duplicates)} previous import(s) used the '
                f'same Outscraper filters.',
            )
        except (AutomaticPipelineError, Exception) as exc:
            data_import.status = DataImport.Status.FAILED
            data_import.error_message = str(exc)
            data_import.save()
            messages.error(request, f'Failed to parse file: {exc}')
            return redirect('pipeline:campaign_detail', pk=data_import.campaign_id)

        return redirect('pipeline:select_columns', import_pk=data_import.pk)

    def get(self, request, import_pk):
        data_import = get_object_or_404(DataImport, pk=import_pk)
        if data_import.status != DataImport.Status.AWAITING_CONFIRM:
            return redirect('pipeline:import_detail', import_pk=import_pk)
        duplicates = find_matching_imports(
            data_import.filter_fingerprint, exclude_pk=data_import.pk
        )
        return render(request, 'pipeline/upload_confirm_duplicate.html', _duplicate_confirm_context(
            data_import.campaign,
            data_import,
            duplicates,
            is_automatic=data_import.campaign.is_automatic,
            request=request,
        ))


class AutomaticResultsView(View):
    """Post-upload screen for automatic campaigns: downloads + MV status."""

    def get(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related(
                'campaign',
                'cleaned_dataset__verification_job',
            ).prefetch_related('cleaned_dataset__verification_job__exports'),
            pk=import_pk,
        )
        if not data_import.campaign.is_automatic:
            return redirect('pipeline:import_detail', import_pk=import_pk)

        _, missing_cols = resolve_automatic_columns(data_import.columns)
        verification_job = None
        if hasattr(data_import, 'cleaned_dataset'):
            verification_job = getattr(
                data_import.cleaned_dataset, 'verification_job', None
            )

        preview, preview_error = _load_import_preview(data_import)
        cleaned_preview, cleaned_preview_error = _load_cleaned_preview(
            getattr(data_import, 'cleaned_dataset', None)
        )

        return render(request, 'pipeline/automatic_results.html', {
            'data_import': data_import,
            'missing_columns': missing_cols,
            'columns_used': data_import.selected_columns,
            'verification_job': verification_job,
            'preview': preview,
            'preview_error': preview_error,
            'cleaned_preview': cleaned_preview,
            'cleaned_preview_error': cleaned_preview_error,
            'add_sources_form': AddMoreSourceFilesForm(),
        })


class ImportAddSourcesView(View):
    """Append more CSV/Excel files to an existing import and re-merge."""

    def post(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('campaign'),
            pk=import_pk,
        )
        form = AddMoreSourceFilesForm(request.POST, request.FILES)
        if not form.is_valid():
            for err in form.errors.get('source_files', form.errors.get('__all__', [])):
                messages.error(request, err)
            return _redirect_import_page(data_import, section='merge-report')

        uploaded = form.cleaned_data['source_files']
        try:
            report = _append_source_files_and_rematch(data_import, uploaded)
            cleaned_rows = _refresh_cleaned_after_merge(data_import)
        except Exception as exc:
            messages.error(request, f'Could not merge new files: {exc}')
            return _redirect_import_page(data_import, section='merge-report')

        formula = (report or {}).get('formula', '')
        matched = (report or {}).get('matched_pairs', 0)
        msg = (
            f'Added {len(uploaded)} file(s). '
            f'{formula or f"Now {data_import.row_count} rows"} '
            f'(matched {matched}).'
        )
        if cleaned_rows is not None:
            msg += f' Cleaned export refreshed ({cleaned_rows} rows). Re-run MillionVerifier if needed.'
        else:
            msg += ' Recreate cleaned export (step 2) to include new columns/rows.'
        messages.success(request, msg)
        return _redirect_import_page(data_import, section='merge-report')


class SelectColumnsView(View):
    def get(self, request, import_pk):
        data_import = get_object_or_404(DataImport, pk=import_pk)
        if data_import.campaign.is_automatic:
            return redirect('pipeline:automatic_results', import_pk=import_pk)
        if data_import.status != DataImport.Status.PARSED:
            messages.error(request, 'This import is not ready for column selection.')
            return redirect('pipeline:campaign_detail', pk=data_import.campaign_id)

        initial = data_import.selected_columns or [
            c for c in SUGGESTED_COLUMNS if c in data_import.columns
        ]
        form = ColumnSelectionForm(
            columns=data_import.columns,
            initial={'selected_columns': initial},
        )
        return render(request, 'pipeline/select_columns.html', {
            'data_import': data_import,
            'form': form,
            'suggested': [c for c in SUGGESTED_COLUMNS if c in data_import.columns],
        })

    def post(self, request, import_pk):
        data_import = get_object_or_404(DataImport, pk=import_pk)
        if data_import.campaign.is_automatic:
            return redirect('pipeline:automatic_results', import_pk=import_pk)
        form = ColumnSelectionForm(data_import.columns, request.POST)
        if not form.is_valid():
            return render(request, 'pipeline/select_columns.html', {
                'data_import': data_import,
                'form': form,
                'suggested': [c for c in SUGGESTED_COLUMNS if c in data_import.columns],
            })

        selected = form.cleaned_data['selected_columns']
        data_import.selected_columns = selected
        data_import.save()

        try:
            csv_bytes, row_count = build_cleaned_csv(
                data_import.original_file.path,
                selected,
            )
        except Exception as exc:
            messages.error(request, f'Cleaning failed: {exc}')
            return redirect('pipeline:select_columns', import_pk=data_import.pk)

        cleaned, _ = CleanedDataset.objects.update_or_create(
            data_import=data_import,
            defaults={'row_count': row_count},
        )
        filename = f'cleaned_{data_import.campaign_id}_{data_import.pk}.csv'
        cleaned.file.save(filename, ContentFile(csv_bytes), save=True)

        messages.success(request, f'Cleaned export ready ({row_count} rows).')
        return _redirect_import_page(data_import, section='cleaned-export')


class ImportDetailView(View):
    def get(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related(
                'campaign',
                'cleaned_dataset__verification_job',
                'cleaned_dataset__phone_verification_job',
            ).prefetch_related(
                'cleaned_dataset__verification_job__exports',
                'source_files',
            ),
            pk=import_pk,
        )
        if data_import.campaign.is_automatic:
            return redirect('pipeline:automatic_results', import_pk=import_pk)
        verification_job = None
        phone_verification_job = None
        if hasattr(data_import, 'cleaned_dataset'):
            verification_job = getattr(
                data_import.cleaned_dataset, 'verification_job', None
            )
            phone_verification_job = getattr(
                data_import.cleaned_dataset, 'phone_verification_job', None
            )

        preview, preview_error = _load_import_preview(data_import)
        cleaned_preview, cleaned_preview_error = _load_cleaned_preview(
            getattr(data_import, 'cleaned_dataset', None)
        )

        return render(request, 'pipeline/import_detail.html', {
            'data_import': data_import,
            'verification_job': verification_job,
            'phone_verification_job': phone_verification_job,
            **_filter_analysis_ui_context(data_import),
            'millionverifier_ready': _millionverifier_configured(),
            'phone_verifier_ready': _phone_validation_configured(),
            'smartlead_ready': _smartlead_configured(),
            'simpletexting_ready': _simpletexting_configured(),
            'xverify_ready': _xverify_configured(),
            'simpletexting_source': resolve_simpletexting_source(
                data_import,
                xverify_configured=_xverify_configured(),
            ),
            'simpletexting_list': _simpletexting_list_status(data_import.campaign.name),
            'ghl_ready': _ghl_configured(),
            'ghl_push_ready': ghl_push_ready(
                data_import,
                xverify_configured=_xverify_configured(),
                ghl_configured=_ghl_configured(),
            ),
            'ghl_source_hint': describe_ghl_sources(
                data_import,
                xverify_configured=_xverify_configured(),
            ),
            'preview': preview,
            'preview_error': preview_error,
            'cleaned_preview': cleaned_preview,
            'cleaned_preview_error': cleaned_preview_error,
            'add_sources_form': AddMoreSourceFilesForm(),
        })


class MillionVerifierBulkRunView(View):
    def post(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('campaign', 'cleaned_dataset'),
            pk=import_pk,
        )
        cleaned = getattr(data_import, 'cleaned_dataset', None)
        if not cleaned or not cleaned.file:
            messages.error(request, 'Create a cleaned export first.')
            return _redirect_import_page(data_import, section='millionverifier')
        if not _millionverifier_configured():
            messages.error(request, 'MillionVerifier API key is missing.')
            return _redirect_import_page(data_import, section='millionverifier')

        try:
            df = pd.read_csv(cleaned.file.path, dtype=str, keep_default_na=False).fillna('')
            candidates = [c for c in ['email', 'email_1', 'email_2', 'email_3'] if c in df.columns]
            if not candidates:
                messages.error(request, 'No email column found in cleaned CSV (expected email/email_1/email_2/email_3).')
                return _redirect_import_page(data_import, section='millionverifier')
            emails: list[str] = []
            for _, row in df.iterrows():
                for col in candidates:
                    val = str(row.get(col, '')).strip()
                    if val:
                        emails.append(val)
                        break
            emails = sorted({e.lower(): e for e in emails}.values())
            limit = int(getattr(settings, 'MILLIONVERIFIER_UPLOAD_ROW_LIMIT', 5) or 5)
            if limit <= 0:
                # Safety: never upload huge files by accident in the UI action.
                # Set MILLIONVERIFIER_UPLOAD_ROW_LIMIT explicitly to disable.
                limit = 5
            emails = emails[:limit]
            if not emails:
                messages.error(request, 'No emails found in cleaned CSV.')
                return _redirect_import_page(data_import, section='millionverifier')

            buf = io.StringIO()
            buf.write('email\n')
            for e in emails:
                buf.write(f'{e}\n')
            csv_bytes = buf.getvalue().encode('utf-8')

            job, _ = VerificationJob.objects.get_or_create(cleaned_dataset=cleaned)
            job.status = VerificationJob.Status.PROCESSING
            job.error_message = ''
            job.status_column = ''
            job.completed_at = None
            job.save()

            up = upload_csv(settings.MILLIONVERIFIER_API_KEY, csv_bytes, filename=f'mv_{data_import.pk}.csv')
            wait_until_done(settings.MILLIONVERIFIER_API_KEY, up.file_id, timeout_seconds=180, poll_every_seconds=3.0)
            report_bytes = download_report_csv(settings.MILLIONVERIFIER_API_KEY, up.file_id, filter_name='all')

            job.source_file.save(
                f'mv_report_{data_import.campaign_id}_{data_import.pk}.csv',
                ContentFile(report_bytes),
                save=True,
            )

            detected_col, exports = split_verification_results(job.source_file.path)
            job.status_column = detected_col
            job.exports.all().delete()
            for category, (out_bytes, row_count) in sorted(exports.items()):
                exp = VerificationExport(job=job, category=category, row_count=row_count)
                exp.file.save(
                    f'{category}_{data_import.campaign_id}_{data_import.pk}.csv',
                    ContentFile(out_bytes),
                    save=True,
                )
            job.status = VerificationJob.Status.COMPLETED
            job.completed_at = timezone.now()
            job.save()

            messages.success(request, f'MillionVerifier complete. Uploaded {len(emails)} emails (test limit applied).')
        except MillionVerifierBulkError as exc:
            messages.error(request, f'MillionVerifier failed: {exc}')
        except Exception as exc:
            messages.error(request, f'MillionVerifier failed: {exc}')
        return _redirect_import_page(data_import, section='millionverifier')


class SmartleadPushGoodEmailsView(View):
    def post(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('campaign', 'cleaned_dataset__verification_job'),
            pk=import_pk,
        )
        if not _smartlead_configured():
            messages.error(request, 'Smartlead API key is missing.')
            return _redirect_import_page(data_import, section='smartlead')
        job = getattr(getattr(data_import, 'cleaned_dataset', None), 'verification_job', None)
        if not job or job.status != VerificationJob.Status.COMPLETED:
            messages.error(request, 'Run MillionVerifier first (step 3).')
            return _redirect_import_page(data_import, section='smartlead')
        good = job.exports.filter(category='good').first()
        if not good or not good.file:
            messages.error(request, 'No "good" export found. Download MV report and confirm categories.')
            return _redirect_import_page(data_import, section='smartlead')

        try:
            df = pd.read_csv(good.file.path, dtype=str, keep_default_na=False).fillna('')
            email_col = None
            for c in df.columns:
                if c.strip().lower() in {'email', 'email_address', 'email address'}:
                    email_col = c
                    break
            if not email_col:
                email_col = df.columns[0] if len(df.columns) else None
            if not email_col:
                messages.error(request, 'Good export has no columns.')
                return _redirect_import_page(data_import, section='smartlead')

            emails = [str(x).strip() for x in df[email_col].tolist()]
            emails = [e for e in emails if e]
            emails = list(dict.fromkeys(emails))  # preserve order unique
            if not emails:
                messages.error(request, 'No emails found in "good" export.')
                return _redirect_import_page(data_import, section='smartlead')

            camp_resp = create_campaign(settings.SMARTLEAD_API_KEY, data_import.campaign.name)
            campaign_id = camp_resp.get('id') or camp_resp.get('campaign_id') or camp_resp.get('campaignId')
            if not campaign_id:
                raise SmartleadError(f'Could not read campaign id from response: {camp_resp}')

            leads = [{"email": e} for e in emails[:400]]
            lead_resp = add_leads(settings.SMARTLEAD_API_KEY, campaign_id, leads)
            messages.success(
                request,
                f'Smartlead: created campaign {campaign_id} and pushed {len(leads)} leads.',
            )
        except SmartleadError as exc:
            messages.error(request, f'Smartlead failed: {exc}')
        except Exception as exc:
            messages.error(request, f'Smartlead failed: {exc}')
        return _redirect_import_page(data_import, section='smartlead')


class XVerifyPhonesView(View):
    def post(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('campaign', 'cleaned_dataset'),
            pk=import_pk,
        )
        cleaned = getattr(data_import, 'cleaned_dataset', None)
        if not cleaned or not cleaned.file:
            messages.error(request, 'Create a cleaned export first.')
            return _redirect_import_page(data_import, section='phone-verifier')
        if not _xverify_configured():
            messages.error(request, 'XVerify is missing config (PHONE_VALIDATION_API_KEY or XVERIFY_DOMAIN).')
            return _redirect_import_page(data_import, section='phone-verifier')

        try:
            df = pd.read_csv(cleaned.file.path, dtype=str, keep_default_na=False).fillna('')
            phone_cols = [c for c in ['phone', 'phone_1', 'phone_2', 'phone_3'] if c in df.columns]
            if not phone_cols:
                messages.error(request, 'No phone column found in cleaned CSV (expected phone/phone_1/phone_2/phone_3).')
                return _redirect_import_page(data_import, section='phone-verifier')

            phones: list[str] = []
            for _, row in df.iterrows():
                for col in phone_cols:
                    val = str(row.get(col, '')).strip()
                    if val:
                        phones.append(val)
                        break
            phones = list(dict.fromkeys(phones))
            limit = int(getattr(settings, 'MILLIONVERIFIER_UPLOAD_ROW_LIMIT', 0) or 0)
            if limit and limit > 0:
                phones = phones[:limit]
            else:
                phones = phones[:5]
            if not phones:
                messages.error(request, 'No phone numbers found in cleaned CSV.')
                return _redirect_import_page(data_import, section='phone-verifier')

            job, _ = PhoneVerificationJob.objects.get_or_create(cleaned_dataset=cleaned)
            job.status = PhoneVerificationJob.Status.PROCESSING
            job.error_message = ''
            job.completed_at = None
            job.save()

            result_rows: list[dict] = []
            valid_count = 0
            for p in phones:
                row = {'input_phone': p, 'response': {}, 'error': ''}
                try:
                    res = verify_phone(
                        settings.PHONE_VALIDATION_API_KEY,
                        settings.XVERIFY_DOMAIN,
                        p,
                    )
                    row['response'] = res
                    if is_valid_status(str(res.get('status') or '')):
                        valid_count += 1
                except XVerifyError as exc:
                    row['error'] = str(exc)
                result_rows.append(row)

            csv_bytes = build_results_csv(result_rows)
            fname = f'xverify_{data_import.campaign_id}_{data_import.pk}.csv'
            job.results_file.save(fname, ContentFile(csv_bytes), save=False)
            job.total_count = len(phones)
            job.valid_count = valid_count
            job.status = PhoneVerificationJob.Status.COMPLETED
            job.completed_at = timezone.now()
            job.save()

            messages.success(
                request,
                f'XVerify complete. {valid_count}/{len(phones)} numbers valid — download results below.',
            )
        except XVerifyError as exc:
            if cleaned and hasattr(cleaned, 'phone_verification_job'):
                job = cleaned.phone_verification_job
                job.status = PhoneVerificationJob.Status.FAILED
                job.error_message = str(exc)
                job.save()
            messages.error(request, f'XVerify failed: {exc}')
        except Exception as exc:
            if cleaned:
                job, _ = PhoneVerificationJob.objects.get_or_create(cleaned_dataset=cleaned)
                job.status = PhoneVerificationJob.Status.FAILED
                job.error_message = str(exc)
                job.save()
            messages.error(request, f'XVerify failed: {exc}')
        return _redirect_import_page(data_import, section='phone-verifier')


class DownloadXVerifyResultsView(View):
    def get(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('cleaned_dataset__phone_verification_job'),
            pk=import_pk,
        )
        job = getattr(
            getattr(data_import, 'cleaned_dataset', None),
            'phone_verification_job',
            None,
        )
        if not job or job.status != PhoneVerificationJob.Status.COMPLETED or not job.results_file:
            raise Http404('No XVerify results for this import.')
        return FileResponse(
            job.results_file.open('rb'),
            as_attachment=True,
            filename=job.results_file.name.split('/')[-1],
        )


class SimpleTextingPushPhonesView(View):
    def post(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related(
                'campaign',
                'cleaned_dataset__phone_verification_job',
                'cleaned_dataset__verification_job',
            ).prefetch_related('cleaned_dataset__verification_job__exports'),
            pk=import_pk,
        )
        if not _simpletexting_configured():
            messages.error(request, 'SimpleTexting API key is missing.')
            return _redirect_import_page(data_import, section='simpletexting')

        xverify_on = _xverify_configured()
        source = resolve_simpletexting_source(data_import, xverify_configured=xverify_on)
        if not source:
            if xverify_on:
                messages.error(
                    request,
                    'No verified phone numbers found. Run XVerify first (step 4), '
                    'or complete MillionVerifier (step 3).',
                )
            else:
                messages.error(
                    request,
                    'Run MillionVerifier first (step 3), then push good emails here for testing.',
                )
            return _redirect_import_page(data_import, section='simpletexting')

        contacts = collect_simpletexting_contacts(data_import, source=source)
        if not contacts:
            if source == 'mv_good':
                messages.error(
                    request,
                    'No contacts with both a good email and a phone number were found. '
                    'Ensure your cleaned export has phone columns, or run XVerify when available.',
                )
            else:
                messages.error(request, 'XVerify ran but no valid phone numbers were found.')
            return _redirect_import_page(data_import, section='simpletexting')

        try:
            list_id, created_new = get_or_create_list(
                settings.SIMPLETEXTING_API_KEY,
                data_import.campaign.name[:41],
            )
            ok = 0
            for contact in contacts[:400]:
                create_contact_on_lists(
                    settings.SIMPLETEXTING_API_KEY,
                    contact['phone'],
                    [list_id],
                    email=contact.get('email', ''),
                )
                ok += 1
            source_label = (
                'MillionVerifier good emails (testing)'
                if source == 'mv_good'
                else 'XVerify good phones'
            )
            action = 'Created contact list' if created_new else 'Updated contact list'
            list_info = ''
            try:
                details = get_list_details(settings.SIMPLETEXTING_API_KEY, list_id)
                total = details.get('totalContactsCount')
                if total is not None:
                    list_info = f' List now has {total} contact(s) in SimpleTexting.'
            except SimpleTextingError:
                pass
            messages.success(
                request,
                f'{action} "{data_import.campaign.name[:41]}" — added {ok} contact(s) '
                f'from {source_label}.{list_info} '
                f'Find it in SimpleTexting under Contacts → Lists (not Campaigns). '
                f'To send SMS, create a Campaign in SimpleTexting and select this list.',
            )
        except SimpleTextingError as exc:
            messages.error(request, f'SimpleTexting failed: {exc}')
        except Exception as exc:
            messages.error(request, f'SimpleTexting failed: {exc}')
        return _redirect_import_page(data_import, section='simpletexting')


class GoHighLevelPushContactsView(View):
    def post(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related(
                'campaign',
                'cleaned_dataset__phone_verification_job',
                'cleaned_dataset__verification_job',
            ).prefetch_related('cleaned_dataset__verification_job__exports'),
            pk=import_pk,
        )
        if not _ghl_configured():
            messages.error(
                request,
                'GoHighLevel is not configured. Set GHL_API_KEY and GHL_LOCATION_ID in .env.',
            )
            return _redirect_import_page(data_import, section='gohighlevel')

        xverify_on = _xverify_configured()
        if not ghl_push_ready(data_import, xverify_configured=xverify_on, ghl_configured=True):
            messages.error(request, 'Run MillionVerifier (step 3) or XVerify (step 4) first.')
            return _redirect_import_page(data_import, section='gohighlevel')

        contacts = collect_ghl_contacts(data_import, xverify_configured=xverify_on)
        if not contacts:
            messages.error(
                request,
                'No contacts with email or phone found. Complete verification steps first.',
            )
            return _redirect_import_page(data_import, section='gohighlevel')

        tag = (data_import.campaign.name or 'import')[:40].strip() or 'import'
        ok = 0
        errors = 0
        first_error = ''
        try:
            for contact in contacts[:400]:
                try:
                    upsert_contact(
                        settings.GHL_API_KEY,
                        settings.GHL_LOCATION_ID,
                        email=contact.get('email', ''),
                        phone=contact.get('phone', ''),
                        first_name=contact.get('first_name', ''),
                        last_name=contact.get('last_name', ''),
                        company_name=contact.get('company', ''),
                        tags=[tag],
                    )
                    ok += 1
                except GoHighLevelError as exc:
                    errors += 1
                    if not first_error:
                        first_error = str(exc)

            if ok == 0:
                detail = f' First error: {first_error}' if first_error else ''
                messages.error(
                    request,
                    f'GoHighLevel push failed for all {len(contacts[:400])} contact(s).'
                    f'{detail} '
                    f'Common cause: invalid emails in MillionVerifier good export — '
                    f'contacts with valid phones only are still pushed after this fix; retry.',
                )
            else:
                note = f' Upserted with tag "{tag}".'
                if errors:
                    note += f' {errors} contact(s) failed.'
                messages.success(
                    request,
                    f'GoHighLevel: pushed {ok} contact(s) to your sub-account.{note} '
                    f'Find them in GHL → Contacts (filter by tag).',
                )
        except GoHighLevelError as exc:
            messages.error(request, f'GoHighLevel failed: {exc}')
        except Exception as exc:
            messages.error(request, f'GoHighLevel failed: {exc}')
        return _redirect_import_page(data_import, section='gohighlevel')


class DownloadDianaQueueView(View):
    """CSV of rows missing email or phone (full Outscraper row + diana_reason)."""

    def get(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('campaign'),
            pk=import_pk,
        )
        if data_import.status != DataImport.Status.PARSED:
            messages.error(request, 'Import must be parsed before exporting Diana queue.')
            return _redirect_import_page(data_import, section='diana')
        if not data_import.original_file:
            raise Http404('No original file.')

        try:
            csv_bytes, row_count = build_diana_handoff_csv(
                data_import.original_file.path
            )
        except Exception as exc:
            messages.error(request, f'Diana export failed: {exc}')
            return _redirect_import_page(data_import, section='diana')

        fname = f'diana_handoff_campaign{data_import.campaign_id}_{data_import.pk}.csv'
        return HttpResponse(
            csv_bytes,
            content_type='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{fname}"',
                'X-Row-Count': str(row_count),
            },
        )


class DownloadOriginalView(View):
    def get(self, request, import_pk):
        data_import = get_object_or_404(DataImport, pk=import_pk)
        if not data_import.original_file:
            raise Http404('No original file available.')
        name = data_import.original_filename or data_import.original_file.name.split('/')[-1]
        return FileResponse(
            data_import.original_file.open('rb'),
            as_attachment=True,
            filename=name,
        )


class DownloadCleanedView(View):
    def get(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('cleaned_dataset'),
            pk=import_pk,
        )
        cleaned = getattr(data_import, 'cleaned_dataset', None)
        if not cleaned or not cleaned.file:
            raise Http404('No cleaned file available.')
        return FileResponse(
            cleaned.file.open('rb'),
            as_attachment=True,
            filename=cleaned.file.name.split('/')[-1],
        )


class VerificationUploadView(View):
    def post(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related('campaign', 'cleaned_dataset'),
            pk=import_pk,
        )
        cleaned = getattr(data_import, 'cleaned_dataset', None)
        if not cleaned:
            messages.error(request, 'Create a cleaned export first.')
            return _redirect_import_page(data_import, section='millionverifier')

        form = VerificationUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, 'Invalid verification file.')
            return _redirect_import_page(data_import, section='millionverifier')

        job, _ = VerificationJob.objects.get_or_create(cleaned_dataset=cleaned)
        job.source_file = form.cleaned_data['source_file']
        job.status = VerificationJob.Status.PROCESSING
        job.error_message = ''
        job.save()

        source_path = job.source_file.path
        status_col = form.cleaned_data.get('status_column', '').strip() or None

        try:
            detected_col, exports = split_verification_results(
                source_path, status_column=status_col
            )
            job.status_column = detected_col
            job.exports.all().delete()
            for category, (csv_bytes, row_count) in sorted(exports.items()):
                export = VerificationExport(
                    job=job,
                    category=category,
                    row_count=row_count,
                )
                fname = (
                    f'{category}_{data_import.campaign_id}_{data_import.pk}.csv'
                )
                export.file.save(fname, ContentFile(csv_bytes), save=True)

            job.status = VerificationJob.Status.COMPLETED
            job.completed_at = timezone.now()
            job.save()
            messages.success(
                request,
                f'Split into {len(exports)} files: '
                f'{", ".join(sorted(exports.keys()))}.',
            )
        except Exception as exc:
            job.status = VerificationJob.Status.FAILED
            job.error_message = str(exc)
            job.save()
            messages.error(request, f'Verification split failed: {exc}')

        return _redirect_import_page(data_import, section='millionverifier')


class DownloadVerificationExportView(View):
    def get(self, request, export_pk):
        export = get_object_or_404(VerificationExport, pk=export_pk)
        return FileResponse(
            export.file.open('rb'),
            as_attachment=True,
            filename=f'{export.category}_{export.file.name.split("/")[-1]}',
        )


class DownloadVerificationZipView(View):
    """Single ZIP containing all MillionVerifier split CSV files."""

    def get(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related(
                'cleaned_dataset__verification_job',
            ).prefetch_related('cleaned_dataset__verification_job__exports'),
            pk=import_pk,
        )
        job = getattr(
            getattr(data_import, 'cleaned_dataset', None),
            'verification_job',
            None,
        )
        if not job or job.status != VerificationJob.Status.COMPLETED:
            raise Http404('Complete a MillionVerifier split first.')
        exports = list(job.exports.all())
        if not exports:
            raise Http404('No verification CSV files.')

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for exp in exports:
                with exp.file.open('rb') as f:
                    zf.writestr(f'{exp.category}.csv', f.read())

        fname = (
            f'verification_splits_campaign{data_import.campaign_id}_'
            f'import{data_import.pk}.zip'
        )
        return HttpResponse(
            buf.getvalue(),
            content_type='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{fname}"'},
        )


class FilterAnalysisRunView(View):
    """Run OpenAI analysis comparing Outscraper filters to database history."""

    def post(self, request, import_pk):
        data_import = get_object_or_404(DataImport, pk=import_pk)
        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER')
        if not next_url:
            next_url = reverse('pipeline:import_detail', kwargs={'import_pk': import_pk})

        if not _openai_configured():
            messages.error(request, 'OpenAI API key is missing (set OPENAI_API_KEY).')
            return redirect(next_url)

        try:
            run_filter_analysis(data_import)
            messages.success(request, 'AI filter analysis complete — see results below.')
        except OpenAIAnalysisError as exc:
            messages.error(request, f'AI analysis failed: {exc}')
        except Exception as exc:
            messages.error(request, f'AI analysis failed: {exc}')
        return redirect(next_url)


class CategorySuggestView(View):
    def get(self, request):
        q = request.GET.get('q', '')
        return JsonResponse({
            'results': suggest_categories(q, limit=12),
        })


class CategoryRecordView(View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        if name:
            record_category(name)
        return JsonResponse({'ok': True})


class LocationSuggestView(View):
    def get(self, request):
        q = request.GET.get('q', '')
        country = request.GET.get('country', 'US')
        return JsonResponse({
            'results': suggest_locations(q, country=country, limit=15),
        })


class LocationRecordView(View):
    def post(self, request):
        country = request.POST.get('country', 'US')
        label = request.POST.get('label', '').strip()
        code = request.POST.get('code', '').strip()
        is_custom = request.POST.get('is_custom', 'false').lower() == 'true'
        if label:
            record_location(
                country=country,
                label=label,
                code=code or label,
                is_custom=is_custom,
            )
        return JsonResponse({'ok': True})
