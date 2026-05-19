"""Outscraper-style location data and serialization."""

from __future__ import annotations

import re

# (code, label) — code used in stored value and pill display
COUNTRY_CHOICES: list[tuple[str, str]] = [
    ('US', 'United States (US)'),
    ('CA', 'Canada (CA)'),
    ('GB', 'United Kingdom (GB)'),
    ('AU', 'Australia (AU)'),
    ('DE', 'Germany (DE)'),
    ('FR', 'France (FR)'),
    ('OTHER', 'Other'),
]

US_STATE_CHOICES: list[tuple[str, str]] = [
    ('AL', 'Alabama'), ('AK', 'Alaska'), ('AZ', 'Arizona'), ('AR', 'Arkansas'),
    ('CA', 'California'), ('CO', 'Colorado'), ('CT', 'Connecticut'), ('DE', 'Delaware'),
    ('FL', 'Florida'), ('GA', 'Georgia'), ('HI', 'Hawaii'), ('ID', 'Idaho'),
    ('IL', 'Illinois'), ('IN', 'Indiana'), ('IA', 'Iowa'), ('KS', 'Kansas'),
    ('KY', 'Kentucky'), ('LA', 'Louisiana'), ('ME', 'Maine'), ('MD', 'Maryland'),
    ('MA', 'Massachusetts'), ('MI', 'Michigan'), ('MN', 'Minnesota'), ('MS', 'Mississippi'),
    ('MO', 'Missouri'), ('MT', 'Montana'), ('NE', 'Nebraska'), ('NV', 'Nevada'),
    ('NH', 'New Hampshire'), ('NJ', 'New Jersey'), ('NM', 'New Mexico'), ('NY', 'New York'),
    ('NC', 'North Carolina'), ('ND', 'North Dakota'), ('OH', 'Ohio'), ('OK', 'Oklahoma'),
    ('OR', 'Oregon'), ('PA', 'Pennsylvania'), ('RI', 'Rhode Island'), ('SC', 'South Carolina'),
    ('SD', 'South Dakota'), ('TN', 'Tennessee'), ('TX', 'Texas'), ('UT', 'Utah'),
    ('VT', 'Vermont'), ('VA', 'Virginia'), ('WA', 'Washington'), ('WV', 'West Virginia'),
    ('WI', 'Wisconsin'), ('WY', 'Wyoming'), ('DC', 'District of Columbia'),
]

REGIONS_BY_COUNTRY: dict[str, list[tuple[str, str]]] = {
    'US': US_STATE_CHOICES,
    'CA': [
        ('AB', 'Alberta'), ('BC', 'British Columbia'), ('MB', 'Manitoba'),
        ('NB', 'New Brunswick'), ('NL', 'Newfoundland and Labrador'),
        ('NS', 'Nova Scotia'), ('NT', 'Northwest Territories'), ('NU', 'Nunavut'),
        ('ON', 'Ontario'), ('PE', 'Prince Edward Island'), ('QC', 'Quebec'),
        ('SK', 'Saskatchewan'), ('YT', 'Yukon'),
    ],
}

COUNTRY_LABELS = dict(COUNTRY_CHOICES)
US_STATE_LABELS = dict(US_STATE_CHOICES)

_CUSTOM_PREFIX = 'custom:'


def format_location(country: str, regions: list[str], *, custom_text: str = '') -> str:
    """Serialize picker value for DB / fingerprinting."""
    if custom_text.strip():
        return f'{_CUSTOM_PREFIX}{custom_text.strip()}'
    country = (country or 'US').upper()
    codes = [_normalize_code(r) for r in regions if _normalize_code(r)]
    if not codes:
        return ''
    return f'{country}|{",".join(codes)}'


def parse_location(value: str) -> dict:
    """
    Parse stored location into picker state.
    Returns: country, regions (list of codes), custom (bool), custom_text (str)
    """
    raw = (value or '').strip()
    if not raw:
        return {'country': 'US', 'regions': [], 'custom': False, 'custom_text': ''}

    if raw.lower().startswith(_CUSTOM_PREFIX):
        return {
            'country': 'US',
            'regions': [],
            'custom': True,
            'custom_text': raw[len(_CUSTOM_PREFIX):].strip(),
        }

    if '|' in raw:
        country, _, rest = raw.partition('|')
        country = country.strip().upper() or 'US'
        regions = [_normalize_code(p) for p in rest.split(',') if p.strip()]
        return {'country': country, 'regions': regions, 'custom': False, 'custom_text': ''}

    # Legacy free-text (e.g. "Westchester, NY" or "LA")
    if len(raw) <= 3 and raw.upper() in US_STATE_LABELS:
        return {'country': 'US', 'regions': [raw.upper()], 'custom': False, 'custom_text': ''}
    return {'country': 'US', 'regions': [], 'custom': True, 'custom_text': raw}


def display_location(value: str, *, max_pills: int = 4) -> str:
    """Human-readable label for tables and headers."""
    state = parse_location(value)
    if state['custom']:
        return state['custom_text'] or '—'

    country = state['country']
    regions = state['regions']
    if not regions:
        return '—'

    country_label = COUNTRY_LABELS.get(country, country)
    labels = []
    region_map = dict(REGIONS_BY_COUNTRY.get(country, US_STATE_CHOICES))
    for code in regions:
        labels.append(region_map.get(code, code))

    if len(labels) <= max_pills:
        region_str = ', '.join(labels)
    else:
        shown = ', '.join(labels[:max_pills])
        region_str = f'{shown} (+{len(labels) - max_pills} more)'

    return f'{country_label} — {region_str}'


def _normalize_code(value: str) -> str:
    v = value.strip().upper()
    if len(v) == 2 and v.isalpha():
        return v
    # Match full state name to code
    for code, name in US_STATE_CHOICES:
        if name.lower() == value.strip().lower():
            return code
    return v
