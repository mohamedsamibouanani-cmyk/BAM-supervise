from django import forms

from .models import Motif, PredictionMotif, Systeme


class MultiCauseValidationForm(forms.Form):
    """Validation d'un dossier de synchronisation.

    ENVOI et ATTRIBUT peuvent exposer plusieurs causes simultanées. SERVICE reste
    volontairement un constat de désynchronisation sans motif métier à choisir.
    """

    predictions = forms.ModelMultipleChoiceField(
        queryset=PredictionMotif.objects.none(),
        required=False,
        label='Causes détectées',
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
        label='Cause retenue',
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
        label='Système responsable',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    decision = forms.ChoiceField(
        choices=[
            ('ACCEPTE', 'Confirmer la sélection'),
            ('MODIFIE', 'Ajuster le diagnostic'),
            ('INCONNU', 'À investiguer'),
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

        if anomaly.niveau == anomaly.Niveau.SERVICE:
            self.fields['decision'].choices = [
                ('ACCEPTE', 'Confirmer'),
                ('MODIFIE', 'Changer le système concerné'),
                ('INCONNU', 'À investiguer'),
            ]

        def label_for(prediction):
            explanation = prediction.explication or {}
            if anomaly.niveau == anomaly.Niveau.SERVICE:
                targets = explanation.get('systemes_a_corriger') or []
                suffix = f' · absent dans {", ".join(targets)}' if targets else ''
                return f'Service {anomaly.code_service} non synchronisé{suffix}'
            if explanation.get('role_diagnostic') == 'CONSTAT_ATTRIBUT':
                return prediction.motif.libelle
            score = float(prediction.score_confiance) * 100
            return f'{prediction.motif.libelle} · {score:.0f}%'

        self.fields['predictions'].label_from_instance = label_for

        final_prediction_ids = list(
            anomaly.validations.filter(est_finale=True, prediction_retenue__isnull=False)
            .values_list('prediction_retenue_id', flat=True)
        )
        if final_prediction_ids:
            defaults = list(qs.filter(pk__in=final_prediction_ids).order_by('rang'))
        else:
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

    def _clean_service(self, cleaned):
        decision = cleaned.get('decision')
        prediction = cleaned.get('prediction') or self.anomaly.predictions.order_by('rang').first()
        system = cleaned.get('systeme_a_corriger_final')

        if decision == 'ACCEPTE':
            if prediction is None:
                raise forms.ValidationError('Aucun constat de désynchronisation n’est disponible.')
            cleaned['prediction'] = prediction
            cleaned['predictions_selectionnees'] = [prediction]
            cleaned['motif_final'] = prediction.motif  # classification technique interne
            if prediction.systeme_a_corriger_predit_id:
                cleaned['systeme_a_corriger_final'] = prediction.systeme_a_corriger_predit
            elif not system:
                codes = (prediction.explication or {}).get('systemes_a_corriger') or []
                if codes:
                    cleaned['systeme_a_corriger_final'] = Systeme.objects.filter(
                        code_systeme=codes[0], actif=True
                    ).first()
        elif decision == 'MODIFIE':
            cleaned['predictions_selectionnees'] = []
            technical = Motif.objects.filter(code_motif='SERVICE_ABSENT', actif=True).first()
            if technical is None:
                raise forms.ValidationError('Le référentiel technique SERVICE_ABSENT doit être initialisé.')
            cleaned['motif_final'] = technical
            cleaned['nouveau_motif'] = ''
        else:  # INCONNU
            cleaned['predictions_selectionnees'] = []
            unknown = Motif.objects.filter(code_motif='MOTIF_INCONNU', actif=True).first()
            if unknown is None:
                raise forms.ValidationError('Le motif technique MOTIF_INCONNU doit être initialisé.')
            cleaned['motif_final'] = unknown
            cleaned['nouveau_motif'] = ''

        if not cleaned.get('systeme_a_corriger_final'):
            raise forms.ValidationError('Sélectionnez le système concerné.')
        return cleaned

    def clean(self):
        cleaned = super().clean()
        if self.anomaly.niveau == self.anomaly.Niveau.SERVICE:
            return self._clean_service(cleaned)

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
            elif multi_mode and self.anomaly.predictions.exists():
                raise forms.ValidationError('Sélectionnez au moins une cause à valider.')
            elif not multi_mode:
                prediction = self.anomaly.predictions.order_by('rang').first()
                if prediction:
                    selected = [prediction]

            if prediction is not None:
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
            elif motif and cleaned.get('systeme_a_corriger_final'):
                cleaned['prediction'] = None
                cleaned['predictions_selectionnees'] = []
            else:
                raise forms.ValidationError('Aucune cause exploitable n’est disponible.')

        elif decision == 'MODIFIE':
            cleaned['predictions_selectionnees'] = []
            if nouveau_motif:
                cleaned['motif_final'] = None
            elif not motif:
                raise forms.ValidationError('Sélectionnez une cause existante ou saisissez la cause réelle.')
            if not cleaned.get('systeme_a_corriger_final'):
                raise forms.ValidationError('Sélectionnez le système responsable.')

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