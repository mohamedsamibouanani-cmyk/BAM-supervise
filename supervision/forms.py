from pathlib import Path

from django import forms
from .models import ContactGroupe, Motif, PredictionMotif, Systeme


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_XLSX_SIGNATURE = b'PK\x03\x04'
_XLS_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


class CampaignImportForm(forms.Form):
    fichier_smi = forms.FileField(
        label='Fichier SMI',
        error_messages={'required': 'Le fichier SMI est manquant.'},
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'}),
    )
    fichier_sicom = forms.FileField(
        label='Fichier SICOM',
        error_messages={'required': 'Le fichier SICOM est manquant.'},
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'}),
    )
    fichier_sibo = forms.FileField(
        label='Fichier SIBO',
        error_messages={'required': 'Le fichier SIBO est manquant.'},
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
        queryset=PredictionMotif.objects.none(), required=False, label='Diagnostic proposé',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    motif_final = forms.ModelChoiceField(
        queryset=Motif.objects.filter(actif=True), required=False, label='Motif retenu',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    nouveau_motif = forms.CharField(
        required=False,
        max_length=255,
        label='Cause réelle',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Décrivez la cause si aucun motif existant ne convient',
        }),
    )
    systeme_a_corriger_final = forms.ModelChoiceField(
        queryset=Systeme.objects.filter(actif=True), label='Système responsable',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    decision = forms.ChoiceField(
        choices=[('ACCEPTE', 'Accepter'), ('MODIFIE', 'Modifier'), ('INCONNU', 'À investiguer')],
        initial='ACCEPTE',
        widget=forms.RadioSelect,
    )
    commentaire = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 3,
            'class': 'form-control',
            'placeholder': 'Ajouter une précision métier (facultatif)',
        }),
        required=False,
    )

    def __init__(self, anomaly, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anomaly = anomaly
        self.fields['prediction'].queryset = anomaly.predictions.select_related(
            'motif', 'systeme_a_corriger_predit'
        ).order_by('rang')
        self.fields['prediction'].label_from_instance = (
            lambda p: f'#{p.rang} {p.motif.libelle} ({float(p.score_confiance)*100:.0f}%)'
        )
        if anomaly.niveau == anomaly.Niveau.SERVICE:
            self.fields['decision'].choices = [
                ('ACCEPTE', 'Confirmer'),
                ('MODIFIE', 'Changer le système concerné'),
                ('INCONNU', 'À investiguer'),
            ]

    def clean(self):
        cleaned = super().clean()
        prediction = cleaned.get('prediction')
        decision = cleaned.get('decision')
        motif = cleaned.get('motif_final')
        nouveau_motif = (cleaned.get('nouveau_motif') or '').strip()

        # Niveau SERVICE : aucun motif métier n'est demandé au superviseur. Le motif
        # SERVICE_ABSENT est uniquement une classification technique interne pour
        # conserver le routage et la traçabilité avec le schéma actuel.
        if self.anomaly.niveau == self.anomaly.Niveau.SERVICE:
            technical = Motif.objects.filter(code_motif='SERVICE_ABSENT', actif=True).first()
            if not technical:
                raise forms.ValidationError('Le référentiel technique SERVICE_ABSENT doit être initialisé.')
            if prediction is None:
                prediction = self.anomaly.predictions.order_by('rang').first()
                if prediction is not None:
                    cleaned['prediction'] = prediction
            cleaned['motif_final'] = technical
            cleaned['nouveau_motif'] = ''
            if decision == 'ACCEPTE' and prediction is not None and prediction.systeme_a_corriger_predit_id:
                cleaned['systeme_a_corriger_final'] = prediction.systeme_a_corriger_predit
            if not cleaned.get('systeme_a_corriger_final'):
                raise forms.ValidationError('Sélectionnez le système où le service est désynchronisé.')
            return cleaned

        if decision == 'ACCEPTE':
            if prediction is None:
                prediction = self.anomaly.predictions.select_related(
                    'motif', 'systeme_a_corriger_predit'
                ).order_by('rang').first()
                if prediction is not None:
                    cleaned['prediction'] = prediction
            if prediction is not None:
                cleaned['motif_final'] = prediction.motif
                if prediction.systeme_a_corriger_predit_id:
                    cleaned['systeme_a_corriger_final'] = prediction.systeme_a_corriger_predit
            elif not motif:
                raise forms.ValidationError('Sélectionnez un diagnostic ou un motif à accepter.')
        elif decision == 'INCONNU':
            unknown = Motif.objects.filter(code_motif='MOTIF_INCONNU').first()
            if not unknown:
                raise forms.ValidationError('Le motif MOTIF_INCONNU doit être initialisé.')
            cleaned['motif_final'] = unknown
        elif decision == 'MODIFIE':
            if nouveau_motif:
                cleaned['motif_final'] = None
            elif not motif:
                raise forms.ValidationError('Sélectionnez un motif existant ou saisissez la cause réelle.')

        if not cleaned.get('systeme_a_corriger_final'):
            raise forms.ValidationError('Sélectionnez le système responsable.')
        return cleaned


class ContactGroupeForm(forms.ModelForm):
    class Meta:
        model = ContactGroupe
        fields = ['nom_complet', 'email', 'fonction']
        widgets = {
            'nom_complet': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nom du collaborateur'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'prenom.nom@example.ma'}),
            'fonction': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Fonction ou spécialité'}),
        }
