from django import template

from dashboard.services.verification_store import mv_display_items

register = template.Library()


@register.filter
def lead_cell(record, column: str) -> str:
    """Return a lead row cell by dynamic column name."""
    try:
        val = record.cell(column)
    except Exception:
        val = ''
    return val if val else '—'


@register.simple_tag
def mv_email_statuses(record) -> list:
    """Per-email MillionVerifier badges for a lead row."""
    return mv_display_items(record)
