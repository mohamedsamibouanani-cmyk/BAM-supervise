from pathlib import Path

from django import forms
from .models import ContactGroupe, Motif, PredictionMotif, Systeme


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_XLSX_SIGNATURE = b'PK\x03\x04'
_XLS_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


class CampaignImportForm(forms.Form):
    fichier_smi = forms.FileField(
        label='Fichier SMI (.xlsx/.xls)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'}),
    )
    fichier_sicom = forms.FileField(
        label='Fichier SICOM (.xlsx/.xls)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'}),
    )
    fichier_sibo = forms.FileField(
        label='Fichier SIBO (.xlsx/.xls)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'}),
    )

    def clean(self):
        cleaned = super().clean()
        for key in ('fichier_smi', 'fichier_sicom', 'fichier_sibo'):
            uploaded = cleaned.get(key)
            if not uploaded:
                continue

            extension = Path(uploaded.name).suffix.lower()
            if extension not in {'.xlsx', '.xls'}:
                self.add_error(key, 'Le fichier doit être un classeur Excel .xlsx ou .xls.')
                continue

            if uploaded.size > MAX_UPLOAD_BYTES:
                self.add_error(key, 'Le fichier dépasse la taille maximale autorisée de 20 Mo.')
                continue

            # Do not trust the filename alone. Validate the container signature before
            # Pandas/OpenPyXL/xlrd parse attacker-controlled bytes.
            position = uploaded.tell()
            header = uploaded.read(8)
            uploaded.seek(position)
            valid_signature = (
                extension == '.xlsx' and header.startswith(_XLSX_SIGNATURE)
            ) or (
                extension == '.xls' and header.startswith(_XLS_SIGNATURE)
            )
            if not valid_signature:
                self.add_error(key, 'Le contenu du fichier ne correspond pas à un classeur Excel valide.')

        return cleaned


class ValidationMotifForm(forms.Form):
    prediction = forms.ModelChoiceField(
        queryset=PredictionMotif.objects.none(), required=False, label='Proposition retenue',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    motif_final = forms.ModelChoiceField(
        queryset=Motif.objects.filter(actif=True), required=False, label='Motif final',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    systeme_a_corriger_final = forms.ModelChoiceField(
        queryset=Systeme.objects.filter(actif=True), label='Système à corriger',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    decision = forms.ChoiceField(
        choices=[('ACCEPTE', 'Accepter'), ('MODIFIE', 'Modifier'), ('INCONNU', 'Motif inconnu')],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    commentaire = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4, 'class': 'form-control', 'placeholder': 'Justification ou précision métier (facultatif)'}),
        required=False,
    )

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
        widgets = {
            'nom_complet': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nom du collaborateur'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'prenom.nom@example.ma'}),
            'fonction': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Fonction ou spécialité'}),
            'actif': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
