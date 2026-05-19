"""Outscraper enrichment services catalog (for upload UI + history tags)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnrichmentService:
    id: str
    title: str
    description: str
    price: str
    unit: str
    icon: str  # Bootstrap Icons class
    recommended: bool = False


# Keys align with Outscraper service ids where possible.
ENRICHMENT_SERVICES: tuple[EnrichmentService, ...] = (
    EnrichmentService(
        'contacts_n_leads',
        'Leads & Contacts Enrichment',
        'Finds contacts for each company — emails, phones, names, job titles, and social links.',
        '$0–0.003', '/contact', 'bi-person-lines-fill', recommended=True,
    ),
    EnrichmentService(
        'emails_validator_service',
        'Email Address Verifier',
        'Validates emails, checks deliverability, filters blacklists and spam traps.',
        '$0–0.003', '/email', 'bi-envelope-check', recommended=True,
    ),
    EnrichmentService(
        'company_insights_service',
        'Company Insights',
        'Firmographics: revenue, size, founding year, public status, and more.',
        '$0–0.005', '/company', 'bi-building', recommended=True,
    ),
    EnrichmentService(
        'whitepages_phones',
        'Phone Identity Finder',
        'Insights about phone number owners (name, address, etc.).',
        '$0–0.003', '/phone', 'bi-telephone', recommended=True,
    ),
    EnrichmentService(
        'phones_enricher_service',
        'Phone Numbers Enricher',
        'Carrier data, validation, and message deliverability.',
        '$0–0.005', '/phone', 'bi-phone-vibrate',
    ),
    EnrichmentService(
        'ai_chain_info',
        'Chain Info',
        'Whether a business is part of a chain (true/false for targeting).',
        '$0–0.005', '/company', 'bi-diagram-3',
    ),
    EnrichmentService(
        'similarweb',
        'SimilarWeb Scraper',
        'Traffic statistics, engagement metrics, and audience insights per domain.',
        '$0–0.005', '/domain', 'bi-graph-up', recommended=True,
    ),
    EnrichmentService(
        'builtwith',
        'BuiltWith Scraper',
        'Detailed information about a website’s technology stack.',
        '$0–0.003', '/domain', 'bi-code-slash',
    ),
    EnrichmentService(
        'whitepages_addresses',
        'Whitepages Addresses Scraper',
        'Insights about addresses and their residents.',
        '$0–0.003', '/address', 'bi-house',
    ),
    EnrichmentService(
        'fastbackgroundcheck_addresses',
        'Fastbackgroundcheck Addresses Scraper',
        'Insights about addresses and their residents.',
        '$0–0.015', '/address', 'bi-house-door',
    ),
    EnrichmentService(
        'geocoding',
        'Geocoding',
        'Human-readable addresses to map coordinates (latitude, longitude).',
        '$0–0.002', '/search', 'bi-geo-alt',
    ),
    EnrichmentService(
        'disposable_email_checker',
        'Disposable Email Checker',
        'Checks if emails are disposable, free, or corporate.',
        '$0–0.0003', '/email', 'bi-shield-exclamation',
    ),
    EnrichmentService(
        'domaininfo',
        'Domain Information',
        'Domain registration and related metadata.',
        '$0–0.003', '/domain', 'bi-globe2',
    ),
    EnrichmentService(
        'zoominfo_domains',
        'Zoominfo by Domains',
        'Company data from ZoomInfo using domain names.',
        '$0–0.005', '/domain', 'bi-briefcase',
    ),
    EnrichmentService(
        'trustpilot_service',
        'Trustpilot Scraper',
        'Returns data from a list of businesses on Trustpilot.',
        '$0–0.003', '/domain', 'bi-star',
    ),
    EnrichmentService(
        'google_maps_reviews_sentiment',
        'Google Maps Reviews Sentiment Analysis',
        'Analyzes reviews into clear, actionable insights.',
        '$0–0.25', '/report', 'bi-chat-square-quote',
    ),
    EnrichmentService(
        'google_maps_reviews_summary',
        'Google Maps Reviews Summary',
        'Summarizes reviews to uncover sentiment and key themes.',
        '$0–0.25', '/report', 'bi-chat-left-text',
    ),
    EnrichmentService(
        'trustpilot_reviews_sentiment',
        'Trustpilot Reviews Sentiment Analysis',
        'Analyzes Trustpilot reviews into actionable insights.',
        '$0–0.25', '/report', 'bi-chat-heart',
    ),
    EnrichmentService(
        'trustpilot_reviews_summary',
        'Trustpilot Reviews Summary',
        'Summarizes Trustpilot reviews for sentiment and themes.',
        '$0–0.25', '/report', 'bi-chat-dots',
    ),
)

SERVICE_BY_ID = {s.id: s for s in ENRICHMENT_SERVICES}
RECOMMENDED_IDS = [s.id for s in ENRICHMENT_SERVICES if s.recommended]

# Legacy keys from early app versions → current Outscraper-style ids.
LEGACY_SERVICE_MAP: dict[str, str] = {
    'email_verifier': 'emails_validator_service',
    'phone_identity': 'whitepages_phones',
    'phones_enricher': 'phones_enricher_service',
    'whitepages': 'whitepages_phones',
    'addresses': 'whitepages_addresses',
    'emails_contacts': 'contacts_n_leads',
    'geocoding': 'geocoding',
    'company_insights': 'company_insights_service',
}

OUTSCRAPER_SERVICE_CHOICES = [(s.id, s.title) for s in ENRICHMENT_SERVICES]


def normalize_service_ids(service_ids: list[str] | None) -> list[str]:
    """Map legacy ids and expand all_services for fingerprints / display."""
    if not service_ids:
        return []
    normalized: set[str] = set()
    for raw in service_ids:
        if raw == 'all_services':
            normalized.update(SERVICE_BY_ID.keys())
            continue
        mapped = LEGACY_SERVICE_MAP.get(raw, raw)
        if mapped in SERVICE_BY_ID:
            normalized.add(mapped)
    return sorted(normalized)


def service_label(service_id: str) -> str:
    mapped = LEGACY_SERVICE_MAP.get(service_id, service_id)
    svc = SERVICE_BY_ID.get(mapped)
    return svc.title if svc else service_id.replace('_', ' ').title()


def service_labels(service_ids: list[str] | None) -> list[str]:
    return [service_label(s) for s in normalize_service_ids(service_ids)]
