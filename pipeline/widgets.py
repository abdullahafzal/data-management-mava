import json

from django import forms

from .services.advanced_params import QUICK_FILTER_CHOICES, normalize_quick_filters
from .services.enrichment_services import (
    ENRICHMENT_SERVICES,
    RECOMMENDED_IDS,
    normalize_service_ids,
)
from .services.locations import COUNTRY_CHOICES, REGIONS_BY_COUNTRY, parse_location


class CategoryPickerWidget(forms.Widget):
    """Outscraper-style multi-category pill input."""

    template_name = 'pipeline/widgets/category_picker.html'

    SUGGESTIONS = [
        'auto body shop',
        'restaurant',
        'scrap metal dealer',
        'doctor',
        'dentist',
        'plumber',
        'lawyer',
    ]

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        raw = (value or '').strip()
        tags = [p.strip() for p in raw.split(',') if p.strip()] if raw else []
        context['widget']['tags'] = tags
        context['widget']['value'] = value or ''
        context['widget']['suggestions'] = self.SUGGESTIONS
        return context

    class Media:
        js = (
            'pipeline/js/picker-autocomplete.js',
            'pipeline/js/category-picker.js',
        )
        css = {'all': ('pipeline/css/location-picker.css',)}


class LocationPickerWidget(forms.Widget):
    """Outscraper-style country + multi-region pill picker."""

    template_name = 'pipeline/widgets/location_picker.html'

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        state = parse_location(value or '')
        regions_payload = {
            code: [{'code': c, 'label': lbl} for c, lbl in regions]
            for code, regions in REGIONS_BY_COUNTRY.items()
        }
        context['widget']['countries'] = COUNTRY_CHOICES
        context['widget']['country'] = state['country']
        context['widget']['regions'] = state['regions']
        context['widget']['regions_json'] = json.dumps(regions_payload)
        context['widget']['custom'] = state['custom']
        context['widget']['custom_text'] = state['custom_text']
        context['widget']['value'] = value or ''
        return context

    class Media:
        js = (
            'pipeline/js/picker-autocomplete.js',
            'pipeline/js/location-picker.js',
        )
        css = {'all': ('pipeline/css/location-picker.css',)}


class EnrichmentServicesWidget(forms.Widget):
    """Outscraper-style selectable enrichment service cards."""

    template_name = 'pipeline/widgets/enrichment_services.html'
    allow_multiple_selected = True

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        if value is None:
            value = []
        elif isinstance(value, str):
            value = [value]
        elif not isinstance(value, (list, tuple)):
            value = list(value)
        selected = set(normalize_service_ids(value))
        context['widget']['services'] = ENRICHMENT_SERVICES
        context['widget']['selected'] = selected
        context['widget']['recommended_ids'] = RECOMMENDED_IDS
        return context

    def value_from_datadict(self, data, files, name):
        """Use getlist so one or many checkboxes always become a list."""
        try:
            return data.getlist(name)
        except AttributeError:
            raw = data.get(name)
            if raw is None:
                return []
            if isinstance(raw, (list, tuple)):
                return list(raw)
            return [raw]

    def format_value(self, value):
        return [] if value is None else value

    class Media:
        js = ('pipeline/js/enrichment-services.js',)
        css = {'all': ('pipeline/css/enrichment-services.css',)}


class QuickFiltersWidget(forms.Widget):
    """Outscraper-style quick filter pill toggles."""

    template_name = 'pipeline/widgets/quick_filters.html'
    allow_multiple_selected = True

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        if value is None:
            value = []
        elif isinstance(value, str):
            value = [value]
        elif not isinstance(value, (list, tuple)):
            value = list(value)
        selected = set(normalize_quick_filters(list(value)))
        context['widget']['filters'] = [
            {'id': fid, 'label': label} for fid, label in QUICK_FILTER_CHOICES
        ]
        context['widget']['selected'] = selected
        return context

    def value_from_datadict(self, data, files, name):
        try:
            return data.getlist(name)
        except AttributeError:
            raw = data.get(name)
            if raw is None:
                return []
            if isinstance(raw, (list, tuple)):
                return list(raw)
            return [raw]

    def format_value(self, value):
        return [] if value is None else value

    class Media:
        js = ('pipeline/js/quick-filters.js',)
        css = {'all': ('pipeline/css/quick-filters.css',)}
