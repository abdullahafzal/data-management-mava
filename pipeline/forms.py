from django import forms

from .models import Campaign, DataImport
from .services.advanced_params import (
    LANGUAGE_CHOICES,
    QUICK_FILTER_CHOICES,
    RESULT_EXTENSION_CHOICES,
)
from .services.enrichment_services import OUTSCRAPER_SERVICE_CHOICES, normalize_service_ids
from .widgets import (
    CategoryPickerWidget,
    EnrichmentServicesWidget,
    LocationPickerWidget,
    QuickFiltersWidget,
)


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ['name', 'processing_mode', 'notes']
        widgets = {
            'processing_mode': forms.RadioSelect(attrs={'class': 'form-check-input'}),
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Westchester auto body — May 2026',
            }),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        labels = {
            'processing_mode': 'How should this campaign process data?',
        }


class OutscraperFiltersForm(forms.Form):
    """Outscraper Google Maps scraper filters (saved per import for history + duplicates)."""

    outscraper_category = forms.CharField(
        label='Categories / brands',
        help_text='Same categories you selected in Outscraper (e.g. auto body shop, scrap metal dealer).',
        widget=CategoryPickerWidget(),
    )
    outscraper_location = forms.CharField(
        label='Locations',
        help_text='Country plus states/regions, or custom locations.',
        widget=LocationPickerWidget(),
    )
    outscraper_max_results = forms.IntegerField(
        required=False,
        min_value=0,
        label='Maximum results limit',
        help_text='0 = unlimited (same as Outscraper).',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0',
            'style': 'max-width:8rem',
        }),
    )
    outscraper_services = forms.MultipleChoiceField(
        required=False,
        label='Enrichment services',
        help_text='Select every enrichment service you enabled in Outscraper before exporting.',
        choices=OUTSCRAPER_SERVICE_CHOICES,
        widget=EnrichmentServicesWidget(),
    )
    outscraper_quick_filters = forms.MultipleChoiceField(
        required=False,
        label='Quick filters',
        choices=QUICK_FILTER_CHOICES,
        widget=QuickFiltersWidget(),
    )
    outscraper_language = forms.ChoiceField(
        required=False,
        label='Language',
        choices=LANGUAGE_CHOICES,
        initial='en',
        widget=forms.Select(attrs={'class': 'form-select', 'style': 'max-width:16rem'}),
    )
    outscraper_places_per_query = forms.IntegerField(
        required=False,
        min_value=0,
        label='Places per one query search',
        help_text='Same as Outscraper — max places returned per search query.',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '500',
            'style': 'max-width:8rem',
        }),
    )
    outscraper_skip = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        label='Skip',
        help_text='Number of results to skip at the start.',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0',
            'style': 'max-width:8rem',
        }),
    )
    outscraper_delete_duplicates = forms.BooleanField(
        required=False,
        initial=True,
        label='Delete duplicates',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    outscraper_use_zip_codes = forms.BooleanField(
        required=False,
        initial=False,
        label='Use zip codes',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    outscraper_task_title = forms.CharField(
        required=False,
        label='Task title',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Google Maps Scraper',
        }),
    )
    outscraper_result_extension = forms.ChoiceField(
        required=False,
        label='Result extension',
        choices=RESULT_EXTENSION_CHOICES,
        initial='xlsx',
        widget=forms.Select(attrs={'class': 'form-select', 'style': 'max-width:10rem'}),
    )
    outscraper_columns_to_return = forms.CharField(
        required=False,
        label='Columns to return',
        help_text='Comma-separated column names. Leave empty to return all columns.',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Select specific columns, or leave empty for all',
        }),
    )
    extra_tags = forms.CharField(
        required=False,
        label='Task tags',
        help_text='Outscraper task tags, comma-separated.',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter tags',
        }),
    )

    def clean_outscraper_location(self):
        value = (self.cleaned_data.get('outscraper_location') or '').strip()
        if not value:
            raise forms.ValidationError(
                'Select at least one location, or enter custom locations.'
            )
        return value

    def clean_outscraper_category(self):
        value = (self.cleaned_data.get('outscraper_category') or '').strip()
        if not value:
            raise forms.ValidationError('Enter at least one category or query from Outscraper.')
        return value

    def clean_outscraper_services(self):
        raw = self.cleaned_data.get('outscraper_services')
        if raw is None:
            return []
        if isinstance(raw, str):
            raw = [raw]
        return normalize_service_ids(list(raw))


class DataImportUploadForm(OutscraperFiltersForm):
    """Upload Outscraper export with full filter tags (manual and automatic)."""

    original_file = forms.FileField(
        label='Outscraper file',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv,.xlsx,.xls',
        }),
    )
    confirm_duplicate = forms.BooleanField(
        required=False,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, automatic=False, **kwargs):
        super().__init__(*args, **kwargs)
        if automatic:
            self.fields['original_file'].label = 'Outscraper CSV or Excel file'
            self.fields['original_file'].help_text = (
                'Export file from Outscraper after running with the filters above.'
            )


class ColumnSelectionForm(forms.Form):
    selected_columns = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'column-check'}),
        required=True,
        label='Columns to keep in cleaned export',
    )

    def __init__(self, columns, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['selected_columns'].choices = [(c, c) for c in columns]

    def clean_selected_columns(self):
        selected = self.cleaned_data['selected_columns']
        if not selected:
            raise forms.ValidationError('Select at least one column.')
        return selected


class VerificationUploadForm(forms.Form):
    source_file = forms.FileField(
        label='MillionVerifier result file',
        help_text='Upload the CSV/Excel file returned by MillionVerifier after verification.',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv,.xlsx,.xls',
        }),
    )
    status_column = forms.CharField(
        required=False,
        label='Status column (optional)',
        help_text='Leave blank to auto-detect (result, quality, status, etc.).',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. result',
        }),
    )
