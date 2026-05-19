"""Outscraper advanced / other parameters (mirrors Google Maps scraper UI)."""

from __future__ import annotations

QUICK_FILTER_CHOICES: list[tuple[str, str]] = [
    ('only_with_website', 'Only with website'),
    ('only_without_website', 'Only without website'),
    ('operational_only', 'Operational only'),
    ('with_phone', 'With phone'),
    ('verified', 'Verified'),
    ('good_rating', 'Good Rating'),
    ('bad_rating', 'Bad Rating'),
]

QUICK_FILTER_LABELS = dict(QUICK_FILTER_CHOICES)

LANGUAGE_CHOICES: list[tuple[str, str]] = [
    ('en', 'English (en)'),
    ('es', 'Spanish (es)'),
    ('fr', 'French (fr)'),
    ('de', 'German (de)'),
    ('it', 'Italian (it)'),
    ('pt', 'Portuguese (pt)'),
    ('nl', 'Dutch (nl)'),
    ('pl', 'Polish (pl)'),
    ('ru', 'Russian (ru)'),
    ('ja', 'Japanese (ja)'),
    ('zh', 'Chinese (zh)'),
]

RESULT_EXTENSION_CHOICES: list[tuple[str, str]] = [
    ('xlsx', 'XLSX'),
    ('csv', 'CSV'),
    ('json', 'JSON'),
    ('parquet', 'Parquet'),
]

DEFAULT_ADVANCED: dict = {
    'quick_filters': [],
    'language': 'en',
    'places_per_query': None,
    'skip': 0,
    'delete_duplicates': True,
    'use_zip_codes': False,
    'task_title': '',
    'result_extension': 'xlsx',
    'columns_to_return': [],
}


def _split_columns(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace('\n', ',').split(',')]
    return [p for p in parts if p]


def normalize_quick_filters(values: list[str] | None) -> list[str]:
    allowed = {k for k, _ in QUICK_FILTER_CHOICES}
    seen: list[str] = []
    for v in values or []:
        key = str(v).strip()
        if key in allowed and key not in seen:
            seen.append(key)
    return seen


def pack_advanced_params(cleaned_data: dict) -> dict:
    """Build JSON-serializable advanced params from form cleaned_data."""
    places = cleaned_data.get('outscraper_places_per_query')
    skip = cleaned_data.get('outscraper_skip')
    return {
        'quick_filters': normalize_quick_filters(
            cleaned_data.get('outscraper_quick_filters')
        ),
        'language': (cleaned_data.get('outscraper_language') or 'en').strip() or 'en',
        'places_per_query': places if places else None,
        'skip': skip if skip is not None else 0,
        'delete_duplicates': bool(cleaned_data.get('outscraper_delete_duplicates', True)),
        'use_zip_codes': bool(cleaned_data.get('outscraper_use_zip_codes')),
        'task_title': (cleaned_data.get('outscraper_task_title') or '').strip(),
        'result_extension': (
            cleaned_data.get('outscraper_result_extension') or 'xlsx'
        ).strip().lower(),
        'columns_to_return': _split_columns(
            cleaned_data.get('outscraper_columns_to_return') or ''
        ),
    }


def normalize_for_fingerprint(advanced: dict | None) -> dict:
    """Canonical dict for duplicate detection."""
    data = {**DEFAULT_ADVANCED, **(advanced or {})}
    places = data.get('places_per_query')
    skip = data.get('skip')
    return {
        'quick_filters': sorted(normalize_quick_filters(data.get('quick_filters'))),
        'language': (data.get('language') or 'en').lower(),
        'places_per_query': places if places else None,
        'skip': int(skip) if skip is not None else 0,
        'delete_duplicates': bool(data.get('delete_duplicates', True)),
        'use_zip_codes': bool(data.get('use_zip_codes')),
        'task_title': (data.get('task_title') or '').strip().lower(),
        'result_extension': (data.get('result_extension') or 'xlsx').lower(),
        'columns_to_return': sorted(
            c.strip().lower() for c in (data.get('columns_to_return') or []) if c
        ),
    }


def quick_filter_labels(keys: list[str]) -> list[str]:
    return [QUICK_FILTER_LABELS[k] for k in keys if k in QUICK_FILTER_LABELS]


def language_label(code: str) -> str:
    for c, label in LANGUAGE_CHOICES:
        if c == code:
            return label
    return code


def extension_label(code: str) -> str:
    for c, label in RESULT_EXTENSION_CHOICES:
        if c == code:
            return label
    return (code or 'xlsx').upper()


def has_advanced_settings(advanced: dict | None) -> bool:
    if not advanced:
        return False
    norm = normalize_for_fingerprint(advanced)
    defaults = normalize_for_fingerprint(DEFAULT_ADVANCED)
    return norm != defaults


def display_lines(advanced: dict | None) -> list[tuple[str, str]]:
    """Human-readable rows for import detail / duplicate pages."""
    if not advanced:
        return []
    data = {**DEFAULT_ADVANCED, **advanced}
    lines: list[tuple[str, str]] = []

    qf = data.get('quick_filters') or []
    if qf:
        lines.append(('Quick filters', ', '.join(quick_filter_labels(qf))))

    lang = data.get('language')
    if lang and lang != 'en':
        lines.append(('Language', language_label(lang)))

    places = data.get('places_per_query')
    if places:
        lines.append(('Places per query', str(places)))

    skip = data.get('skip')
    if skip:
        lines.append(('Skip', str(skip)))

    if data.get('delete_duplicates') is False:
        lines.append(('Delete duplicates', 'No'))
    if data.get('use_zip_codes'):
        lines.append(('Use zip codes', 'Yes'))

    title = (data.get('task_title') or '').strip()
    if title:
        lines.append(('Task title', title))

    ext = data.get('result_extension')
    if ext and ext != 'xlsx':
        lines.append(('Result extension', extension_label(ext)))

    cols = data.get('columns_to_return') or []
    if cols:
        lines.append(('Columns to return', ', '.join(cols)))

    return lines
