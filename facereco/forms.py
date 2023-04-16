from django import forms
from django.conf import settings
from django.db.models import Q
from django.db.models.functions import datetime
from django.utils import timezone

from .models import Demand, Directory


class DemandForm(forms.ModelForm):
    directory = forms.ModelChoiceField(queryset=Directory.objects.filter(is_visible=True), label='Répertoire', initial=0)
    photo = forms.ImageField(required=True)

    class Meta:
        model = Demand
        fields = ['directory', 'name', 'first_name', 'email', 'photo']

        labels = {
            'name': 'Nom',
            'first_name': 'Prénom',
            'email': 'Email',
            'photo': 'Photo',
        }

    def clean(self):
        cleaned_data = super().clean()
        waiting_date = timezone.now() - timezone.timedelta(minutes=settings.MIN_TIME_BETWEEN_DEMANDS)
        same_demand = Demand.objects.filter((Q(name=cleaned_data.get('name'))
                                             | Q(email=cleaned_data.get('email')))
                                            & Q(date__gte=waiting_date)
                                            & Q(directory=cleaned_data.get('directory'))).exists()
        if same_demand:
            raise forms.ValidationError('Une demande identique a déjà été effectuée récemment. Merci de patienter.')

