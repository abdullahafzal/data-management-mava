"""Build database context about Outscraper filter history for AI analysis."""

from __future__ import annotations

from django.db.models import Q

from ..models import DataImport
from .filters import find_matching_imports


def _import_snapshot(imp: DataImport) -> dict:
    has_cleaned = hasattr(imp, 'cleaned_dataset')
    return {
        'import_id': imp.pk,
        'campaign_id': imp.campaign_id,
        'campaign_name': imp.campaign.name,
        'created_at': imp.created_at.isoformat(),
        'row_count': imp.row_count,
        'category': imp.outscraper_category,
        'location': imp.outscraper_location,
        'max_results': imp.outscraper_max_results,
        'services': imp.outscraper_services or [],
        'extra_tags': imp.extra_tags or [],
        'has_cleaned_export': has_cleaned,
        'filename': imp.original_filename,
    }


def find_similar_imports(
    category: str,
    location: str,
    *,
    exclude_pk: int | None = None,
    limit: int = 8,
) -> list[DataImport]:
    """Imports with overlapping category/location but not necessarily identical filters."""
    qs = DataImport.objects.filter(
        status=DataImport.Status.PARSED,
    ).select_related('campaign')

    cat = (category or '').strip()
    loc = (location or '').strip()
    if cat:
        first_cat = cat.split(',')[0].strip()
        if first_cat:
            qs = qs.filter(outscraper_category__icontains=first_cat)
    if loc:
        qs = qs.filter(outscraper_location__icontains=loc[:64])

    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    return list(qs.order_by('-created_at')[:limit])


def build_analysis_context_from_filters(
    *,
    category: str,
    location: str,
    max_results: int | None,
    services: list[str],
    extra_tags: list[str] | None,
    advanced: dict | None,
    campaign_name: str,
    fingerprint: str,
) -> dict:
    """Pre-upload context: proposed filters are not yet saved as a DataImport."""
    exact: list[DataImport] = []
    if fingerprint:
        exact = find_matching_imports(fingerprint)

    similar = find_similar_imports(category, location)
    similar_pks = {i.pk for i in exact}
    similar = [i for i in similar if i.pk not in similar_pks][:8]

    total_parsed = DataImport.objects.filter(status=DataImport.Status.PARSED).count()
    category_count = DataImport.objects.filter(
        status=DataImport.Status.PARSED,
        outscraper_category__icontains=(category or '')[:80],
    ).count()

    proposed = {
        'campaign_name': campaign_name,
        'category': category,
        'location': location,
        'max_results': max_results,
        'services': services or [],
        'extra_tags': extra_tags or [],
        'advanced': advanced or {},
        'filter_fingerprint': fingerprint,
        'status': 'proposed_before_upload',
    }

    return {
        'proposed_filters': proposed,
        'database_stats': {
            'total_parsed_imports': total_parsed,
            'imports_with_same_category': category_count,
            'exact_duplicate_count': len(exact),
            'similar_import_count': len(similar),
        },
        'exact_matches': [_import_snapshot(i) for i in exact],
        'similar_matches': [_import_snapshot(i) for i in similar],
        'match_type': (
            'exact' if exact else ('similar' if similar else 'none')
        ),
    }


def build_analysis_context(
    data_import: DataImport,
    *,
    exclude_pk: int | None = None,
) -> dict:
    """Collect exact + similar imports and stats for OpenAI."""
    exclude = exclude_pk if exclude_pk is not None else data_import.pk
    fingerprint = data_import.filter_fingerprint

    exact: list[DataImport] = []
    if fingerprint:
        exact = find_matching_imports(fingerprint, exclude_pk=exclude)

    similar = find_similar_imports(
        data_import.outscraper_category,
        data_import.outscraper_location,
        exclude_pk=exclude,
    )
    similar_pks = {i.pk for i in exact}
    similar = [i for i in similar if i.pk not in similar_pks][:8]

    total_parsed = DataImport.objects.filter(status=DataImport.Status.PARSED).count()
    category_count = DataImport.objects.filter(
        status=DataImport.Status.PARSED,
        outscraper_category__icontains=(data_import.outscraper_category or '')[:80],
    ).count()

    proposed = _import_snapshot(data_import)
    proposed['filter_fingerprint'] = fingerprint

    return {
        'proposed_filters': proposed,
        'database_stats': {
            'total_parsed_imports': total_parsed,
            'imports_with_same_category': category_count,
            'exact_duplicate_count': len(exact),
            'similar_import_count': len(similar),
        },
        'exact_matches': [_import_snapshot(i) for i in exact],
        'similar_matches': [_import_snapshot(i) for i in similar],
        'match_type': (
            'exact' if exact else ('similar' if similar else 'none')
        ),
    }
