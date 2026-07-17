from django import template

register = template.Library()


@register.filter
def lead_cell(record, column: str) -> str:
    """Return a lead row cell by dynamic column name."""
    try:
        val = record.cell(column)
    except Exception:
        val = ''
    return val if val else '—'
