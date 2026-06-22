from django import forms

from .services.columns import detect_key_column, detect_status_column
from .services.parser import read_registry_file


class NyRegistryUploadForm(forms.Form):
    source_file = forms.FileField(
        label='NY State registry export',
        help_text='CSV or Excel (.xlsx) from the NY business registry.',
        widget=forms.FileInput(attrs={'class': 'form-control'}),
    )
    key_column = forms.ChoiceField(
        label='Business key column',
        required=False,
        help_text='Unique ID per business (auto-detected if left blank).',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    status_column = forms.ChoiceField(
        label='Status column',
        required=False,
        help_text='Active / dissolved / inactive (auto-detected if left blank).',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    run_ai_summary = forms.BooleanField(
        label='Generate AI summary (small token usage — diff is computed locally)',
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['key_column'].choices = [('', 'Auto-detect')]
        self.fields['status_column'].choices = [('', 'Auto-detect / none')]

    def clean_source_file(self):
        uploaded = self.cleaned_data['source_file']
        name = (uploaded.name or '').lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx') or name.endswith('.xls')):
            raise forms.ValidationError('Upload a CSV or Excel file (.csv, .xlsx, .xls).')
        return uploaded

    def _peek_columns(self):
        uploaded = self.cleaned_data.get('source_file')
        if not uploaded:
            return []
        uploaded.seek(0)
        import tempfile

        suffix = '.csv' if uploaded.name.lower().endswith('.csv') else '.xlsx'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
        uploaded.seek(0)
        try:
            df = read_registry_file(tmp_path)
            return list(df.columns)
        finally:
            import os
            os.unlink(tmp_path)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        columns = self._peek_columns()
        if not columns:
            raise forms.ValidationError('File has no columns or could not be read.')

        col_choices = [('', 'Auto-detect')] + [(c, c) for c in columns]
        self.fields['key_column'].choices = col_choices
        self.fields['status_column'].choices = [('', 'Auto-detect / none')] + [
            (c, c) for c in columns
        ]

        key = cleaned.get('key_column') or detect_key_column(columns)
        if not key:
            raise forms.ValidationError(
                'Could not auto-detect a business key column. '
                'Select one from the dropdown below (e.g. Facility #) and submit again.'
            )
        if key not in columns:
            raise forms.ValidationError(f'Key column "{key}" is not in the file.')

        status = cleaned.get('status_column') or detect_status_column(columns) or ''
        if status and status not in columns:
            status = detect_status_column(columns) or ''

        cleaned['resolved_key_column'] = key
        cleaned['resolved_status_column'] = status
        cleaned['columns'] = columns
        return cleaned
