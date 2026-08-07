from django import forms
from .models import ContactGroupe, Motif, PredictionMotif, Systeme


class CampaignImportForm(forms.Form):
    fichier_smi = forms.FileField(label='Fichier SMI (.xlsx)')
    fichier_sicom = forms.FileField(label='Fichier SICOM (.xlsx)')
    fichier_sibo = forms.FileField(label='Fichier SIBO (.xlsx)')

    def clean(self):
        cleaned = super().clean()
        for key in ('fichier_smi', 'fichier_sicom', 'fichier_sibo'):
            f = cleaned.get(key)
            if f and not f.name.lower().endswith(('.xlsx', '.xls')):
                self.add_error(key, 'Le fichier doit être un classeur Excel.')
        return cleaned


class ValidationMotifForm(forms.Form):
    prediction = forms.ModelChoiceField(queryset=PredictionMotif.objects.none(), required=False, label='Proposition retenue')
    motif_final = forms.ModelChoiceField(queryset=Motif.objects.filter(actif=True), required=False, label='Motif final')
    systeme_a_corriger_final = forms.ModelChoiceField(queryset=Systeme.objects.filter(actif=True), label='Système à corriger')
    decision = forms.ChoiceField(choices=[('ACCEPTE', 'Accepter'), ('MODIFIE', 'Modifier'), ('INCONNU', 'Motif inconnu')])
    commentaire = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)

    def __init__(self, anomaly, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anomaly = anomaly
        self.fields['prediction'].queryset = anomaly.predictions.select_related('motif', 'systeme_a_corriger_predit').order_by('rang')
        self.fields['prediction'].label_from_instance = lambda p: f'#{p.rang} {p.motif.libelle} ({float(p.score_confiance)*100:.0f}%)'

    def clean(self):
        cleaned = super().clean()
        prediction = cleaned.get('prediction')
        decision = cleaned.get('decision')
        motif = cleaned.get('motif_final')
        systeme = cleaned.get('systeme_a_corriger_final')
        if prediction:
            if not motif:
                cleaned['motif_final'] = prediction.motif
            if not systeme and prediction.systeme_a_corriger_predit:
                cleaned['systeme_a_corriger_final'] = prediction.systeme_a_corriger_predit
        if decision == 'INCONNU':
            unknown = Motif.objects.filter(code_motif='MOTIF_INCONNU').first()
            if not unknown:
                raise forms.ValidationError('Le motif MOTIF_INCONNU doit être initialisé.')
            cleaned['motif_final'] = unknown
        if not cleaned.get('motif_final'):
            raise forms.ValidationError('Sélectionnez un motif final ou une prédiction.')
        if not cleaned.get('systeme_a_corriger_final'):
            raise forms.ValidationError('Sélectionnez le système à corriger.')
        return cleaned


class ContactGroupeForm(forms.ModelForm):
    class Meta:
        model = ContactGroupe
        fields = ['nom_complet', 'email', 'fonction', 'actif']
        widgets = {'nom_complet': forms.TextInput(attrs={'placeholder': 'Nom du collaborateur'})}
