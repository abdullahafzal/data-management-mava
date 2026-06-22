from django import forms

from .models import AutomationRun

NAME_MODE_CHOICES = (
    ('full_name', 'Single full name column (auto-split first / middle / last)'),
    ('split_columns', 'Separate first, middle, and last name columns'),
)


class SpyDialerUploadForm(forms.Form):
    source_file = forms.FileField(
        label='Upload CSV or Excel',
        help_text='Up to ~4,000 rows. Progress is saved after every row.',
        widget=forms.FileInput(attrs={'class': 'form-control'}),
    )

    def clean_source_file(self):
        uploaded = self.cleaned_data['source_file']
        name = (uploaded.name or '').lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx') or name.endswith('.xls')):
            raise forms.ValidationError('Upload CSV or Excel (.csv, .xlsx, .xls).')
        return uploaded


class SpyDialerConfigureForm(forms.Form):
    search_mode = forms.ChoiceField(
        label='Search method',
        choices=AutomationRun.SearchMode.choices,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
    )
    people_name_mode = forms.ChoiceField(
        label='Name columns',
        choices=NAME_MODE_CHOICES,
        initial='full_name',
        required=False,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input people-name-mode'}),
    )
    batch_limit = forms.IntegerField(
        label='Batch limit (optional)',
        required=False,
        min_value=1,
        max_value=5000,
        help_text='Leave blank to process all pending rows.',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'All pending'}),
    )
    pause_between = forms.FloatField(
        label='Pause between searches (seconds)',
        required=False,
        initial=2.0,
        min_value=0.5,
        max_value=30.0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
    )

    def __init__(self, *args, columns=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.columns = columns or []
        blank = [('', '— select column —')]
        col_choices = blank + [(c, c) for c in self.columns]

        self.fields['phone'] = forms.ChoiceField(
            label='Phone number column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-phone'}),
        )
        self.fields['full_name'] = forms.ChoiceField(
            label='Full name column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-people column-full-name'}),
        )
        self.fields['first_name'] = forms.ChoiceField(
            label='First name column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-people column-split-name'}),
        )
        self.fields['middle_name'] = forms.ChoiceField(
            label='Middle name column (optional)',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-people column-split-name'}),
        )
        self.fields['last_name'] = forms.ChoiceField(
            label='Last name column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-people column-split-name'}),
        )
        self.fields['city'] = forms.ChoiceField(
            label='City column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-people'}),
        )
        self.fields['address'] = forms.ChoiceField(
            label='Address column (optional if city is set)',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-people'}),
        )
        self.fields['state'] = forms.ChoiceField(
            label='State column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select column-people'}),
        )

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('search_mode')
        if mode == AutomationRun.SearchMode.PHONE:
            if not cleaned.get('phone'):
                self.add_error('phone', 'Select the phone number column.')
        elif mode == AutomationRun.SearchMode.PEOPLE:
            name_mode = cleaned.get('people_name_mode') or 'full_name'
            if name_mode == 'full_name':
                if not cleaned.get('full_name'):
                    self.add_error('full_name', 'Select the full name column (e.g. Owner Name).')
            else:
                for field in ('first_name', 'last_name'):
                    if not cleaned.get(field):
                        self.add_error(field, 'This column is required when using separate name columns.')
            if not cleaned.get('state'):
                self.add_error('state', 'Select the state column.')
            if not cleaned.get('city') and not cleaned.get('address'):
                self.add_error('city', 'Select city or address column.')
        return cleaned

    def column_map(self) -> dict[str, str]:
        cleaned = self.cleaned_data
        if cleaned.get('search_mode') == AutomationRun.SearchMode.PHONE:
            return {'phone': cleaned['phone']}

        name_mode = cleaned.get('people_name_mode') or 'full_name'
        mapping: dict[str, str] = {'name_mode': name_mode, 'state': cleaned.get('state') or ''}

        if name_mode == 'full_name':
            mapping['full_name'] = cleaned.get('full_name') or ''
        else:
            mapping['first_name'] = cleaned.get('first_name') or ''
            mapping['middle_name'] = cleaned.get('middle_name') or ''
            mapping['last_name'] = cleaned.get('last_name') or ''

        if cleaned.get('city'):
            mapping['city'] = cleaned['city']
        if cleaned.get('address'):
            mapping['address'] = cleaned['address']

        return {k: v for k, v in mapping.items() if v}


class IcmUploadForm(forms.Form):
    source_file = forms.FileField(
        label='Upload NY registry CSV/Excel or master workbook',
        help_text=(
            'NY export with Owner Name + Facility City/State, or an existing master '
            'workbook with pipeline_row_id / icm_verified columns.'
        ),
        widget=forms.FileInput(attrs={'class': 'form-control'}),
    )

    def clean_source_file(self):
        uploaded = self.cleaned_data['source_file']
        name = (uploaded.name or '').lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx') or name.endswith('.xls')):
            raise forms.ValidationError('Upload CSV or Excel (.csv, .xlsx, .xls).')
        return uploaded


class IcmConfigureForm(forms.Form):
    people_name_mode = forms.ChoiceField(
        label='Name columns',
        choices=NAME_MODE_CHOICES,
        initial='full_name',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input icm-name-mode'}),
    )
    batch_limit = forms.IntegerField(
        label='Batch limit (optional)',
        required=False,
        min_value=1,
        max_value=5000,
        help_text='Leave blank to process all pending individuals.',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'All pending'}),
    )
    force_rebuild_master = forms.BooleanField(
        label='Rebuild master from upload',
        required=False,
        initial=False,
        help_text='Check after changing the NY registry file. Uncheck to resume from saved master.',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    pause_between = forms.FloatField(
        label='Pause between searches (seconds)',
        required=False,
        initial=2.0,
        min_value=0.5,
        max_value=30.0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
    )

    def __init__(self, *args, columns=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.columns = columns or []
        blank = [('', '— select column —')]
        col_choices = blank + [(c, c) for c in self.columns]

        self.fields['full_name'] = forms.ChoiceField(
            label='Full name column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select icm-col icm-full-name'}),
        )
        self.fields['first_name'] = forms.ChoiceField(
            label='First name column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select icm-col icm-split-name'}),
        )
        self.fields['middle_name'] = forms.ChoiceField(
            label='Middle initial / name column (optional)',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select icm-col icm-split-name'}),
        )
        self.fields['last_name'] = forms.ChoiceField(
            label='Last name column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select icm-col icm-split-name'}),
        )
        self.fields['city'] = forms.ChoiceField(
            label='City column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select icm-col'}),
        )
        self.fields['address'] = forms.ChoiceField(
            label='Address / street column (optional)',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select icm-col'}),
        )
        self.fields['state'] = forms.ChoiceField(
            label='State column',
            required=False,
            choices=col_choices,
            widget=forms.Select(attrs={'class': 'form-select icm-col'}),
        )

    def clean(self):
        cleaned = super().clean()
        name_mode = cleaned.get('people_name_mode') or 'full_name'
        if name_mode == 'full_name':
            if not cleaned.get('full_name'):
                self.add_error('full_name', 'Select the full name column (e.g. Owner Name).')
        else:
            for field in ('first_name', 'last_name'):
                if not cleaned.get(field):
                    self.add_error(field, 'Required when using separate name columns.')
        if not cleaned.get('state'):
            self.add_error('state', 'Select the state column (ICM requires state).')
        if not cleaned.get('city') and not cleaned.get('address'):
            self.add_error('city', 'Select city or address/street column.')
        return cleaned

    def column_map(self) -> dict[str, str]:
        cleaned = self.cleaned_data
        name_mode = cleaned.get('people_name_mode') or 'full_name'
        mapping: dict[str, str] = {
            'name_mode': name_mode,
            'state': cleaned.get('state') or '',
        }
        if name_mode == 'full_name':
            mapping['full_name'] = cleaned.get('full_name') or ''
        else:
            mapping['first_name'] = cleaned.get('first_name') or ''
            mapping['middle_name'] = cleaned.get('middle_name') or ''
            mapping['last_name'] = cleaned.get('last_name') or ''
        for key in ('city', 'address'):
            if cleaned.get(key):
                mapping[key] = cleaned[key]
        return {k: v for k, v in mapping.items() if v}
