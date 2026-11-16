import json

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import ColumnMapping, Organization
from .services import MAX_CSV_BYTES


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)
    organization_name = forms.CharField(max_length=140, label='Business name')
    currency = forms.ChoiceField(choices=Organization.CURRENCIES, initial='CAD')

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'organization_name', 'currency', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account already uses that email address.')
        return email


class UploadForm(forms.Form):
    file = forms.FileField(label='Sales data (CSV)', help_text='UTF-8 CSV, maximum 2 MB and 5,000 data rows.')
    mapping = forms.CharField(required=False, max_length=10000, widget=forms.HiddenInput)
    saved_mapping = forms.ModelChoiceField(queryset=ColumnMapping.objects.none(), required=False,
                                          empty_label='Suggest from column names', label='Saved mapping')
    mapping_name = forms.CharField(required=False, max_length=100, label='Save mapping as (optional)')

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['saved_mapping'].queryset = organization.column_mappings.all()
        self.fields['file'].widget.attrs['accept'] = '.csv,text/csv'

    def clean_mapping(self):
        raw = self.cleaned_data['mapping']
        if not raw:
            return None
        try:
            mapping = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise forms.ValidationError('Invalid column mapping. Choose the file and review the mapping again.') from exc
        if not isinstance(mapping, dict):
            raise forms.ValidationError('Column mapping must be an object containing the five required fields.')
        return mapping

    def clean_file(self):
        uploaded = self.cleaned_data['file']
        if not uploaded.name.lower().endswith('.csv'):
            raise forms.ValidationError('Please upload a .csv file.')
        if uploaded.size > MAX_CSV_BYTES:
            raise forms.ValidationError('Maximum file size is 2 MB.')
        return uploaded
