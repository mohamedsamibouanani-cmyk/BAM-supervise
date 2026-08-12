from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from .forms_anomaly import MultiCauseValidationForm
from .models import (
    Anomalie, ExempleApprentissage, HistoriqueAnomalie, JournalAudit, Motif,
    Notification, Systeme, ValidationMotif,
)
from .services.notifications import send_validation_email


def _audit(request, action, entity, entity_id, new_values=None, old_values=None):
    JournalAudit.objects.create(
        superviseur=request.user if request.user.is_authenticated else None,
        action=action,
        entite=entity,
        id_entite=entity_id,
        anciennes_valeurs=old_values,
        nouvelles_valeurs=new_values,
        adresse_ip=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
    )


def _prediction_target_system(prediction, fallback=None):
    if prediction.systeme_a_corriger_predit_id:
        return prediction.systeme_a_corriger_predit
    codes = (prediction.explication or {}).get('systemes_a_corriger') or []
    if codes:
        system = Systeme.objects.filter(code_systeme=codes[0], actif=True).first()
        if system:
            return system
    return fallback


def _create_learning_example(validation):
    anomaly = validation.anomalie
    ExempleApprentissage.objects.create(
        validation=validation,
        motif_label=validation.motif_final,
        systeme_a_corriger_label=validation.systeme_a_corriger_final,
        caracteristiques={
            'niveau': anomaly.niveau,
            'type_ecart': anomaly.type_ecart,
            'service': anomaly.code_service,
            'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
            'systeme_ecart': anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
        },
        eligible=validation.decision != ValidationMotif.Decision.INCONNU,
        raison_exclusion=(
            'Motif inconnu' if validation.decision == ValidationMotif.Decision.INCONNU else ''
        ),
    )


@login_required
def anomaly_detail(request, pk):
    anomaly = get_object_or_404(
        Anomalie.objects.select_related('systeme_ecart', 'attribut', 'campagne').prefetch_related(
            'details__systeme',
            'predictions__motif',
            'predictions__systeme_a_corriger_predit',
            'validations__motif_final',
            'validations__systeme_a_corriger_final',
            'validations__prediction_retenue',
            'historique',
            'verifications__campagne_controle',
        ),
        pk=pk,
    )
    final_validations = list(
        anomaly.validations.filter(est_finale=True)
        .select_related('motif_final', 'systeme_a_corriger_final', 'prediction_retenue')
        .order_by('version_validation', 'pk')
    )
    latest_validation = final_validations[0] if final_validations else None
    notifications = list(
        Notification.objects.filter(validation__anomalie=anomaly)
        .select_related('groupe__systeme', 'validation')
        .prefetch_related('destinataires')
        .order_by('-creee_le')[:30]
    ) if final_validations else []

    return render(request, 'supervision/anomaly_detail.html', {
        'anomaly': anomaly,
        'latest_validation': latest_validation,
        'final_validations': final_validations,
        'notifications': notifications,
    })


@login_required
@transaction.atomic
def anomaly_validate(request, pk):
    anomaly = get_object_or_404(
        Anomalie.objects.select_related('systeme_ecart', 'attribut'),
        pk=pk,
    )

    if request.method == 'POST':
        form = MultiCauseValidationForm(anomaly, request.POST)
        if form.is_valid():
            anomaly.validations.filter(est_finale=True).update(est_finale=False)
            next_version = (
                anomaly.validations.order_by('-version_validation')
                .values_list('version_validation', flat=True).first() or 0
            ) + 1

            decision = form.cleaned_data['decision']
            comment = form.cleaned_data['commentaire']
            validations = []

            if decision == ValidationMotif.Decision.ACCEPTE:
                selected_predictions = form.cleaned_data.get('predictions_selectionnees') or []
                if selected_predictions:
                    for offset, prediction in enumerate(selected_predictions):
                        target = _prediction_target_system(prediction, anomaly.systeme_ecart)
                        if target is None:
                            continue
                        validation = ValidationMotif.objects.create(
                            anomalie=anomaly,
                            prediction_retenue=prediction,
                            motif_final=prediction.motif,
                            systeme_a_corriger_final=target,
                            superviseur=request.user,
                            decision=ValidationMotif.Decision.ACCEPTE,
                            commentaire=comment,
                            version_validation=next_version + offset,
                            est_finale=True,
                        )
                        _create_learning_example(validation)
                        validations.append(validation)
                else:
                    # Compatibilité avec le workflow historique : une correction
                    # explicite peut être validée même si le moteur n'a créé aucune
                    # PredictionMotif pour ce dossier.
                    validation = ValidationMotif.objects.create(
                        anomalie=anomaly,
                        prediction_retenue=None,
                        motif_final=form.cleaned_data['motif_final'],
                        systeme_a_corriger_final=form.cleaned_data['systeme_a_corriger_final'],
                        superviseur=request.user,
                        decision=ValidationMotif.Decision.ACCEPTE,
                        commentaire=comment,
                        version_validation=next_version,
                        est_finale=True,
                    )
                    _create_learning_example(validation)
                    validations.append(validation)
            else:
                motif_final = form.cleaned_data.get('motif_final')
                nouveau_motif = (form.cleaned_data.get('nouveau_motif') or '').strip()
                final_decision = decision
                if nouveau_motif:
                    motif_final = Motif.objects.filter(libelle__iexact=nouveau_motif).first()
                    if not motif_final:
                        base_code = slugify(nouveau_motif).replace('-', '_').upper()[:60] or 'MOTIF_APPRIS'
                        code = base_code
                        suffix = 2
                        while Motif.objects.filter(code_motif=code).exists():
                            code = f'{base_code[:70]}_{suffix}'
                            suffix += 1
                        motif_final = Motif.objects.create(
                            code_motif=code,
                            libelle=nouveau_motif,
                            description='Motif ajouté après validation humaine dans un dossier de supervision.',
                            niveau_applicable=anomaly.niveau,
                            categorie='APPRENTISSAGE',
                            actif=True,
                        )
                    final_decision = ValidationMotif.Decision.MODIFIE

                validation = ValidationMotif.objects.create(
                    anomalie=anomaly,
                    prediction_retenue=None,
                    motif_final=motif_final,
                    systeme_a_corriger_final=form.cleaned_data['systeme_a_corriger_final'],
                    superviseur=request.user,
                    decision=final_decision,
                    commentaire=comment,
                    version_validation=next_version,
                    est_finale=True,
                )
                _create_learning_example(validation)
                validations.append(validation)

            if not validations:
                form.add_error(None, 'Aucune correction valide n’a pu être enregistrée.')
            else:
                old_status = anomaly.statut
                anomaly.statut = Anomalie.Statut.VALIDEE
                anomaly.save(update_fields=['statut'])

                labels = ', '.join(v.motif_final.libelle for v in validations)
                HistoriqueAnomalie.objects.create(
                    anomalie=anomaly,
                    ancien_statut=old_status,
                    nouveau_statut=Anomalie.Statut.VALIDEE,
                    source_evenement='SUPERVISEUR',
                    superviseur=request.user,
                    commentaire=f'{len(validations)} correction(s) validée(s) : {labels}',
                )

                _audit(request, 'VALIDATE', 'validation_motif', validations[0].pk, {
                    'nb_corrections': len(validations),
                    'corrections': [
                        {
                            'motif': v.motif_final.code_motif,
                            'systeme_principal': v.systeme_a_corriger_final.code_systeme,
                            'prediction': v.prediction_retenue_id,
                        }
                        for v in validations
                    ],
                })

                try:
                    # Le service agrège toutes les validations finales du dossier et
                    # envoie un seul message par système avec uniquement ses corrections.
                    send_validation_email(validations[0])
                    messages.success(
                        request,
                        f'{len(validations)} correction(s) validée(s). Les équipes responsables ont été notifiées.',
                    )
                except Exception as exc:
                    messages.warning(
                        request,
                        f'Corrections validées, mais certaines notifications n’ont pas pu être envoyées : {exc}',
                    )
                return redirect('anomaly_detail', pk=pk)
    else:
        form = MultiCauseValidationForm(anomaly)

    return render(request, 'supervision/validation_form.html', {'form': form, 'anomaly': anomaly})
