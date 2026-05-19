import io
import re
import zipfile

from django.conf import settings
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from .constants import resolve_automatic_columns
from .forms import (
    CampaignForm,
    ColumnSelectionForm,
    DataImportUploadForm,
    VerificationUploadForm,
)
from .models import (
    Campaign,
    CleanedDataset,
    DataImport,
    VerificationExport,
    VerificationJob,
)
from .services.automatic import AutomaticPipelineError, run_automatic_pipeline
from .services.enrichment_services import normalize_service_ids
from .services.locations import REGIONS_BY_COUNTRY, parse_location
from .services.suggestions import (
    record_category,
    record_location,
    suggest_categories,
    suggest_locations,
)
from .services import (
    build_cleaned_csv,
    build_diana_handoff_csv,
    build_filter_fingerprint,
    pack_advanced_params,
    find_matching_imports,
    parse_extra_tags,
    parse_upload,
    preview_upload,
    split_verification_results,
)

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


def _process_upload_file(data_import: DataImport, uploaded_file=None) -> None:
    if uploaded_file is not None:
        data_import.original_filename = uploaded_file.name
    parsed = parse_upload(data_import.original_file.path)
    data_import.columns = parsed['columns']
    data_import.row_count = parsed['row_count']
    data_import.file_format = parsed['file_format']
    data_import.status = DataImport.Status.PARSED
    data_import.save()


class CampaignListView(View):
    def get(self, request):
        campaigns = Campaign.objects.prefetch_related('imports')
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
        upload_form = DataImportUploadForm(
            initial=_upload_form_initial(campaign),
            automatic=campaign.is_automatic,
        )
        return render(request, 'pipeline/campaign_detail.html', {
            'campaign': campaign,
            'upload_form': upload_form,
        })


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

    def _post_automatic(self, request, campaign):
        form = DataImportUploadForm(
            request.POST, request.FILES, automatic=True,
        )
        if not form.is_valid():
            messages.error(request, 'Please fix the errors below.')
            return render(request, 'pipeline/campaign_detail.html', {
                'campaign': campaign,
                'upload_form': form,
            })

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

        data_import = DataImport(campaign=campaign)
        data_import.original_file = form.cleaned_data['original_file']
        data_import.original_filename = form.cleaned_data['original_file'].name
        _apply_filter_fields(data_import, form, system_tags=['automatic'])

        if duplicates and not confirm:
            data_import.status = DataImport.Status.AWAITING_CONFIRM
            data_import.save()
            return render(request, 'pipeline/upload_confirm_duplicate.html', {
                'campaign': campaign,
                'data_import': data_import,
                'duplicates': duplicates,
                'is_automatic': True,
            })

        _sync_campaign_filters(campaign, form)
        _remember_filter_suggestions(form)
        return self._finish_automatic_import(
            request, campaign, data_import, form.cleaned_data['original_file'],
            duplicates=duplicates, confirmed=bool(confirm),
        )

    def _finish_automatic_import(
        self, request, campaign, data_import, uploaded_file,
        *, duplicates, confirmed,
    ):
        data_import.status = DataImport.Status.UPLOADED
        data_import.save()

        try:
            _process_upload_file(data_import, uploaded_file)
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
            return render(request, 'pipeline/campaign_detail.html', {
                'campaign': campaign,
                'upload_form': form,
            })

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

        data_import = DataImport(campaign=campaign)
        data_import.original_file = form.cleaned_data['original_file']
        data_import.original_filename = form.cleaned_data['original_file'].name
        _apply_filter_fields(data_import, form)

        if duplicates and not confirm:
            data_import.status = DataImport.Status.AWAITING_CONFIRM
            data_import.save()
            return render(request, 'pipeline/upload_confirm_duplicate.html', {
                'campaign': campaign,
                'data_import': data_import,
                'duplicates': duplicates,
                'is_automatic': False,
            })

        data_import.status = DataImport.Status.UPLOADED
        data_import.save()
        _sync_campaign_filters(campaign, form)
        _remember_filter_suggestions(form)

        try:
            _process_upload_file(data_import, form.cleaned_data['original_file'])
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
            data_import.original_file.delete(save=False)
            data_import.delete()
            messages.info(request, 'Upload cancelled. No new Outscraper export was added.')
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
        return render(request, 'pipeline/upload_confirm_duplicate.html', {
            'campaign': data_import.campaign,
            'data_import': data_import,
            'duplicates': duplicates,
            'is_automatic': data_import.campaign.is_automatic,
        })


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

        return render(request, 'pipeline/automatic_results.html', {
            'data_import': data_import,
            'missing_columns': missing_cols,
            'columns_used': data_import.selected_columns,
            'verification_job': verification_job,
        })


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
        return redirect('pipeline:import_detail', import_pk=data_import.pk)


class ImportDetailView(View):
    def get(self, request, import_pk):
        data_import = get_object_or_404(
            DataImport.objects.select_related(
                'campaign',
                'cleaned_dataset__verification_job',
            ).prefetch_related(
                'cleaned_dataset__verification_job__exports',
            ),
            pk=import_pk,
        )
        if data_import.campaign.is_automatic:
            return redirect('pipeline:automatic_results', import_pk=import_pk)
        verification_job = None
        if hasattr(data_import, 'cleaned_dataset'):
            verification_job = getattr(
                data_import.cleaned_dataset, 'verification_job', None
            )

        preview = None
        preview_error = ''
        if data_import.original_file:
            try:
                preview = preview_upload(data_import.original_file.path)
            except Exception as exc:
                preview_error = str(exc)

        return render(request, 'pipeline/import_detail.html', {
            'data_import': data_import,
            'verification_job': verification_job,
            'millionverifier_ready': _millionverifier_configured(),
            'phone_verifier_ready': _phone_validation_configured(),
            'preview': preview,
            'preview_error': preview_error,
        })


class DownloadDianaQueueView(View):
    """CSV of rows missing email or phone (full Outscraper row + diana_reason)."""

    def get(self, request, import_pk):
        data_import = get_object_or_404(DataImport, pk=import_pk)
        if data_import.status != DataImport.Status.PARSED:
            messages.error(request, 'Import must be parsed before exporting Diana queue.')
            return redirect('pipeline:import_detail', import_pk=import_pk)
        if not data_import.original_file:
            raise Http404('No original file.')

        try:
            csv_bytes, row_count = build_diana_handoff_csv(
                data_import.original_file.path
            )
        except Exception as exc:
            messages.error(request, f'Diana export failed: {exc}')
            return redirect('pipeline:import_detail', import_pk=import_pk)

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
            DataImport.objects.select_related('cleaned_dataset'),
            pk=import_pk,
        )
        cleaned = getattr(data_import, 'cleaned_dataset', None)
        if not cleaned:
            messages.error(request, 'Create a cleaned export first.')
            return redirect('pipeline:import_detail', import_pk=import_pk)

        form = VerificationUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, 'Invalid verification file.')
            return redirect('pipeline:import_detail', import_pk=import_pk)

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

        return redirect('pipeline:import_detail', import_pk=import_pk)


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
