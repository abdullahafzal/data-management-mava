from django import template

from ..services.locations import display_location

register = template.Library()


@register.filter
def location_display(value):
    return display_location(value)
