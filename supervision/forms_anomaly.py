from django import forms

from .models import Motif, PredictionMotif, Systeme


class MultiCauseValidationForm(forms.Form):
    """Validation métier d'un dossier avec plusieurs causes simultanées.

    Les prédictions issues de règles métier sont des constats indépendants :
    VILLE, TELEPHONE, ARTICLE... peuvent donc être retenus ensemble.
    """

    predictions = forms.ModelMultipleChoiceField(
        queryset=PredictionMotif.objects.none(),
        required=False,
        label='Corrections détectées',
        widget=forms.CheckboxSelectMultiple,
    )
    prediction = forms.ModelChoiceField(
        queryset=PredictionMotif.objects.none(),
        required=False,
        widget=forms.HiddenInput,
    )
    multi_cause_mode = forms.CharField(required=False, initial='1', widget=forms.HiddenInput)
    motif_final = forms.ModelChoiceField(
        queryset=Motif.objects.filter(actif=True),
        required=False,
        label='Motif retenu',
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
        queryset=Systeme.objects.filter(actif=True),
        required=False,
        label='Système à corriger',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    decision = forms.ChoiceField(
        choices=[
            ('ACCEPTE', 'Accepter les corrections sélectionnées'),
            ('MODIFIE', 'Définir une autre correction'),
            ('INCONNU', 'Cause à investiguer'),
        ],
        initial='ACCEPTE',
        widget=forms.RadioSelect,
    )
    commentaire = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'class': 'form-control',
            'placeholder': 'Ajouter une précision métier (facultatif)',
        }),
    )

    def __init__(self, anomaly, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anomaly = anomaly
        qs = anomaly.predictions.select_related(
            'motif', 'systeme_a_corriger_predit'
        ).order_by('rang')
        self.fields['predictions'].queryset = qs
        self.fields['prediction'].queryset = qs

        def label_for(prediction):
            explanation = prediction.explication or {}
            targets = explanation.get('systemes_a_corriger') or []
            if not targets and prediction.systeme_a_corriger_predit_id:
                targets = [prediction.systeme_a_corriger_predit.code_systeme]
            indices = explanation.get('indices') or []
            field = explanation.get('attribut_analyse') or (
                indices[0].get('champ') if indices else ''
            )
            parts = [prediction.motif.libelle]
            if field:
                parts.append(field)
            if targets:
                parts.append(', '.join(targets))
            parts.append(f'{float(prediction.score_confiance) * 100:.0f}%')
            return ' · '.join(parts)

        self.fields['predictions'].label_from_instance = label_for

        final_prediction_ids = list(
            anomaly.validations.filter(est_finale=True, prediction_retenue__isnull=False)
            .values_list('prediction_retenue_id', flat=True)
        )
        if final_prediction_ids:
            defaults = list(qs.filter(pk__in=final_prediction_ids).order_by('rang'))
        else:
            # Les règles effectivement matchées sont cochées ensemble : ce ne sont
            # pas des alternatives probabilistes, mais des écarts simultanés.
            defaults = list(
                qs.filter(source_prediction=PredictionMotif.Source.REGLE)
                .exclude(motif__code_motif='MOTIF_INCONNU')
            )
            if not defaults:
                defaults = list(qs[:1])

        if defaults:
            primary = defaults[0]
            self.fields['predictions'].initial = [p.pk for p in defaults]
            self.fields['prediction'].initial = primary.pk
            self.fields['motif_final'].initial = primary.motif_id
            if primary.systeme_a_corriger_predit_id:
                self.fields['systeme_a_corriger_final'].initial = primary.systeme_a_corriger_predit_id
            else:
                codes = (primary.explication or {}).get('systemes_a_corriger') or []
                if codes:
                    target = Systeme.objects.filter(code_systeme=codes[0], actif=True).first()
                    if target:
                        self.fields['systeme_a_corriger_final'].initial = target.pk

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get('decision')
        selected = sorted(list(cleaned.get('predictions') or []), key=lambda p: p.rang)
        prediction = cleaned.get('prediction')
        motif = cleaned.get('motif_final')
        nouveau_motif = (cleaned.get('nouveau_motif') or '').strip()
        multi_mode = (cleaned.get('multi_cause_mode') or '').strip() == '1'

        if decision == 'ACCEPTE':
            if selected:
                prediction = selected[0]
            elif prediction is not None:
                selected = [prediction]
            elif multi_mode:
                raise forms.ValidationError('Sélectionnez au moins une correction à valider.')
            else:
                prediction = self.anomaly.predictions.order_by('rang').first()
                if prediction:
                    selected = [prediction]

            if prediction is None:
                raise forms.ValidationError('Aucune correction exploitable n’est disponible.')

            cleaned['prediction'] = prediction
            cleaned['predictions_selectionnees'] = selected
            cleaned['motif_final'] = prediction.motif
            if prediction.systeme_a_corriger_predit_id:
                cleaned['systeme_a_corriger_final'] = prediction.systeme_a_corriger_predit
            else:
                codes = (prediction.explication or {}).get('systemes_a_corriger') or []
                if codes:
                    target = Systeme.objects.filter(code_systeme=codes[0], actif=True).first()
                    if target:
                        cleaned['systeme_a_corriger_final'] = target

        elif decision == 'MODIFIE':
            cleaned['predictions_selectionnees'] = []
            if nouveau_motif:
                cleaned['motif_final'] = None
            elif not motif:
                raise forms.ValidationError('Sélectionnez un motif existant ou saisissez la cause réelle.')
            if not cleaned.get('systeme_a_corriger_final'):
                raise forms.ValidationError('Sélectionnez le système à corriger.')

        elif decision == 'INCONNU':
            cleaned['predictions_selectionnees'] = []
            unknown = Motif.objects.filter(code_motif='MOTIF_INCONNU').first()
            if not unknown:
                raise forms.ValidationError('Le motif MOTIF_INCONNU doit être initialisé.')
            cleaned['motif_final'] = unknown
            if not cleaned.get('systeme_a_corriger_final'):
                raise forms.ValidationError('Sélectionnez le système à investiguer.')

        if not cleaned.get('systeme_a_corriger_final'):
            raise forms.ValidationError('Aucun système responsable n’a pu être déterminé.')
        return cleaned
