"""Category & location suggestions (built-in + saved from past imports)."""

from __future__ import annotations

import re

from django.db.models import Q
from django.utils import timezone

from ..models import SavedCategory, SavedLocationSuggestion
from .locations import REGIONS_BY_COUNTRY, US_STATE_CHOICES

DEFAULT_CATEGORIES = [
    'auto body shop',
    'restaurant',
    'scrap metal dealer',
    'doctor',
    'dentist',
    'plumber',
    'lawyer',
    'F&B',
]


def _normalize_name(value: str) -> str:
    return re.sub(r'\s+', ' ', value.strip().lower())


def suggest_categories(query: str = '', *, limit: int = 12) -> list[dict]:
    q = _normalize_name(query)
    seen: set[str] = set()
    results: list[dict] = []

    def add(name: str, source: str) -> None:
        key = _normalize_name(name)
        if not key or key in seen:
            return
        if q and q not in key:
            return
        seen.add(key)
        results.append({'label': name.strip(), 'source': source})

    db_qs = SavedCategory.objects.all()
    if q:
        db_qs = db_qs.filter(name__icontains=q)
    for row in db_qs.order_by('-use_count', 'name')[:limit]:
        add(row.name, 'saved')

    if not q:
        for name in DEFAULT_CATEGORIES:
            add(name, 'default')
            if len(results) >= limit:
                return results[:limit]
    else:
        for name in DEFAULT_CATEGORIES:
            add(name, 'default')

    if q and _normalize_name(q) not in seen:
        results.append({'label': query.strip(), 'source': 'new', 'is_new': True})

    return results[:limit]


def record_category(name: str) -> SavedCategory | None:
    cleaned = re.sub(r'\s+', ' ', (name or '').strip())
    if not cleaned:
        return None
    key = _normalize_name(cleaned)
    obj, created = SavedCategory.objects.get_or_create(
        name_key=key,
        defaults={'name': cleaned},
    )
    if not created:
        obj.name = cleaned
        obj.use_count += 1
        obj.last_used_at = timezone.now()
        obj.save(update_fields=['name', 'use_count', 'last_used_at'])
    return obj


def _region_list(country: str) -> list[tuple[str, str]]:
    return REGIONS_BY_COUNTRY.get(country.upper(), US_STATE_CHOICES)


def suggest_locations(
    query: str = '',
    *,
    country: str = 'US',
    limit: int = 15,
) -> list[dict]:
    q = (query or '').strip().lower()
    country = (country or 'US').upper()
    seen: set[str] = set()
    results: list[dict] = []

    def add(*, code: str, label: str, source: str, is_custom: bool = False) -> None:
        key = f'{country}:{code or label}'.lower()
        if key in seen:
            return
        hay = f'{code} {label}'.lower()
        if q and q not in hay:
            return
        seen.add(key)
        results.append({
            'code': code,
            'label': label,
            'source': source,
            'is_custom': is_custom,
        })

    db_qs = SavedLocationSuggestion.objects.filter(country=country)
    if q:
        db_qs = db_qs.filter(Q(label__icontains=q) | Q(code__icontains=q))
    for row in db_qs.order_by('-use_count', 'label')[:limit]:
        add(
            code=row.code or row.label,
            label=row.label,
            source='saved',
            is_custom=row.is_custom,
        )

    if q:
        for code, label in _region_list(country):
            add(code=code, label=label, source='region')
            if len(results) >= limit:
                break

    if q and len(q) >= 2:
        typed = query.strip()
        typed_key = f'{country}:{typed}'.lower()
        if typed_key not in seen:
            results.append({
                'code': typed,
                'label': typed,
                'source': 'new',
                'is_custom': True,
                'is_new': True,
            })

    return results[:limit]


def record_location(
    *,
    country: str,
    label: str,
    code: str = '',
    is_custom: bool = False,
) -> SavedLocationSuggestion | None:
    country = (country or 'US').upper()
    label = re.sub(r'\s+', ' ', (label or '').strip())
    if not label:
        return None
    code = (code or label).strip()
    if not is_custom and len(code) == 2:
        is_custom = False
    else:
        region_codes = {c for c, _ in _region_list(country)}
        if code.upper() not in region_codes and code == label:
            is_custom = True

    obj, created = SavedLocationSuggestion.objects.get_or_create(
        country=country,
        label=label,
        defaults={'code': code, 'is_custom': is_custom},
    )
    if not created:
        obj.use_count += 1
        obj.last_used_at = timezone.now()
        if code:
            obj.code = code
        obj.save(update_fields=['use_count', 'last_used_at', 'code'])
    return obj
