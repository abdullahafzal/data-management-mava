from django import forms

from .models import LeadSourceFile, LeadWorkspace


class CreateWorkspaceForm(forms.Form):
    name = forms.CharField(
        label='Campaign / database name',
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'e.g. NY lead database',
            'autofocus': True,
        }),
    )
    master_file = forms.FileField(
        label='Master file (DMV / primary CSV or Excel)',
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.csv,.xlsx,.xls',
        }),
        help_text='This becomes the match base. Later uploads enrich matching rows and add new ones.',
    )
    source_kind = forms.ChoiceField(
        label='Master source type',
        choices=LeadSourceFile.SourceKind.choices,
        initial=LeadSourceFile.SourceKind.DMV,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def clean_master_file(self):
        f = self.cleaned_data['master_file']
        name = (getattr(f, 'name', '') or '').lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx') or name.endswith('.xls')):
            raise forms.ValidationError('Use .csv, .xlsx, or .xls.')
        return f


class MergeSourceForm(forms.Form):
    source_file = forms.FileField(
        label='Data file',
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.csv,.xlsx,.xls',
        }),
    )
    source_kind = forms.ChoiceField(
        label='Source',
        choices=LeadSourceFile.SourceKind.choices,
        initial=LeadSourceFile.SourceKind.OUTSCRAPER,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def clean_source_file(self):
        f = self.cleaned_data['source_file']
        name = (getattr(f, 'name', '') or '').lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx') or name.endswith('.xls')):
            raise forms.ValidationError('Use .csv, .xlsx, or .xls.')
        return f
