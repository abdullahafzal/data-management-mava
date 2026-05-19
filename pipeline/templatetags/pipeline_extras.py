from django import template

from pipeline.services.locations import display_location

register = template.Library()


@register.filter
def location_display(value):
    return display_location(value)
