"""
Fixed Outscraper columns used when a campaign runs in **automatic** processing mode.
Only columns that exist in the uploaded file are kept.
"""

# User-facing labels mapped to actual Outscraper column names when they differ.
AUTOMATIC_COLUMN_ALIASES = {
    'email': ['email', 'email_1', 'email_2', 'email_3'],
    'phone number': ['phone', 'phone_1', 'phone_2', 'phone_3'],
}

AUTOMATIC_COLUMNS = [
    'name',
    'query',
    'name_for_emails',
    'site',
    'site.company_insights.address',
    'site.company_insights.city',
    'site.company_insights.country',
    'site.company_insights.linkedin_bio',
    'site.company_insights.name',
    'site.company_insights.phone',
    'site.company_insights.state',
    'site.company_insights.timezone',
    'site.company_insights.zip',
    'site.company_insights.employees',
    'site.company_insights.facebook_company_page',
    'site.company_insights.revenue',
    'site.company_insights.founded_year',
    'category',
    'type',
    'phone.whitepages_phones.address',
    'phone.whitepages_phones.lookup_type',
    'phone.whitepages_phones.name',
    'phone.whitepages_phones.person_id',
    'street',
    'city',
    'latitude',
    'longitude',
    # Resolved via aliases (first match in file wins per alias group):
    'email',
    'phone number',
]


def resolve_automatic_columns(available_columns: list[str]) -> tuple[list[str], list[str]]:
    """
    Return (columns_to_keep, missing_from_file).
    Alias groups (email, phone) pick the first matching column only once.
    """
    available = set(available_columns)
    selected: list[str] = []
    missing: list[str] = []
    used_alias_groups: set[str] = set()

    for col in AUTOMATIC_COLUMNS:
        if col in AUTOMATIC_COLUMN_ALIASES:
            if col in used_alias_groups:
                continue
            used_alias_groups.add(col)
            found = None
            for candidate in AUTOMATIC_COLUMN_ALIASES[col]:
                if candidate in available:
                    found = candidate
                    break
            if found:
                if found not in selected:
                    selected.append(found)
            else:
                missing.append(col)
        elif col in available:
            if col not in selected:
                selected.append(col)
        else:
            missing.append(col)

    return selected, missing
