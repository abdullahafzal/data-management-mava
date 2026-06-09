import hashlib
import json
import re

from ..models import DataImport
from .enrichment_services import OUTSCRAPER_SERVICE_CHOICES, normalize_service_ids
from .locations import format_location, parse_location


def _normalize(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


def _normalize_location(value: str) -> str:
    """Canonical location string for duplicate fingerprinting."""
    state = parse_location(value)
    if state['custom']:
        return _normalize(state['custom_text'])
    return _normalize(
        format_location(state['country'], state['regions'])
    )


def parse_extra_tags(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r'[,;\n]+', raw)
    return sorted({_normalize(p) for p in parts if p.strip()})


def build_filter_fingerprint(
    category: str,
    location: str,
    max_results: int | None,
    services: list[str],
    extra_tags: list[str] | None = None,
    *,
    advanced: dict | None = None,
) -> str:
    from .advanced_params import normalize_for_fingerprint

    payload = {
        'category': _normalize(category),
        'location': _normalize_location(location),
        'max_results': max_results if max_results else None,
        'services': sorted(_normalize(s) for s in normalize_service_ids(services) if s),
        'extra_tags': sorted(extra_tags or []),
        'advanced': normalize_for_fingerprint(advanced),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def find_matching_imports(
    fingerprint: str,
    *,
    exclude_pk: int | None = None,
) -> list[DataImport]:
    qs = DataImport.objects.filter(
        filter_fingerprint=fingerprint,
        status=DataImport.Status.PARSED,
    ).select_related('campaign').order_by('-created_at')
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return list(qs)
