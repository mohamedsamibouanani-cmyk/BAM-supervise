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
from .services.analysis import prediction_target_codes, refresh_prediction_routing
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


def _prediction_target_system(prediction):
    codes = prediction_target_codes(prediction)
    if not codes:
        return None
    return Systeme.objects.filter(code_systeme=codes[0], actif=True).first()


def _create_learning_example(validation):
    anomaly = validation.anomalie
    # Niveau SERVICE = constat de présence uniquement. Il n'existe pas de motif
    # métier à apprendre à ce niveau, donc aucune donnée d'apprentissage motif/cause.
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return None
    return ExempleApprentissage.objects.create(
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
            'predictions__regle__attribut',
            'predictions__regle__flux__systeme_source',
            'predictions__regle__flux__systeme_destination',
            'validations__motif_final',
            'validations__systeme_a_corriger_final',
            'validations__prediction_retenue',
            'historique',
            'verifications__campagne_controle',
        ),
        pk=pk,
    )

    refresh_prediction_routing(anomaly)

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
    refresh_prediction_routing(anomaly)

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
            is_attribute_difference = (
                anomaly.niveau == Anomalie.Niveau.ATTRIBUT
                and anomaly.type_ecart == Anomalie.TypeEcart.DIFFERENT
                and bool(form.cleaned_data.get('systemes_a_corriger_finaux'))
            )

            if decision == ValidationMotif.Decision.ACCEPTE:
                selected_predictions = form.cleaned_data.get('predictions_selectionnees') or []
                if selected_predictions:
                    for offset, prediction in enumerate(selected_predictions):
                        target = _prediction_target_system(prediction)
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
                if nouveau_motif and anomaly.niveau != Anomalie.Niveau.SERVICE:
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

                target_systems = (
                    form.cleaned_data.get('systemes_a_corriger_finaux')
                    if is_attribute_difference
                    else [form.cleaned_data['systeme_a_corriger_final']]
                )
                for offset, target_system in enumerate(target_systems):
                    validation = ValidationMotif.objects.create(
                        anomalie=anomaly,
                        prediction_retenue=None,
                        motif_final=motif_final,
                        systeme_a_corriger_final=target_system,
                        superviseur=request.user,
                        decision=final_decision,
                        commentaire=comment,
                        version_validation=next_version + offset,
                        est_finale=True,
                    )
                    _create_learning_example(validation)
                    validations.append(validation)

            if not validations:
                form.add_error(
                    None,
                    'Aucun système responsable n’est confirmé par les données. '
                    'Révisez le diagnostic au lieu de notifier le système observé.',
                )
            else:
                old_status = anomaly.statut
                anomaly.statut = Anomalie.Statut.VALIDEE
                anomaly.save(update_fields=['statut'])

                if anomaly.niveau == Anomalie.Niveau.SERVICE:
                    targets = ', '.join(dict.fromkeys(
                        v.systeme_a_corriger_final.code_systeme for v in validations
                    ))
                    history_comment = (
                        f'Constat validé : service {anomaly.code_service} non synchronisé. '
                        f'Système(s) concerné(s) : {targets}.'
                    )
                    audit_values = {
                        'niveau': 'SERVICE',
                        'constat': 'SERVICE_ABSENT',
                        'code_service': anomaly.code_service,
                        'systemes_concernes': [
                            v.systeme_a_corriger_final.code_systeme for v in validations
                        ],
                    }
                elif is_attribute_difference:
                    targets = [
                        v.systeme_a_corriger_final.code_systeme for v in validations
                    ]
                    history_comment = (
                        'Constat de valeurs différentes validé. '
                        f'Système(s) à corriger : {", ".join(targets)}.'
                    )
                    audit_values = {
                        'niveau': 'ATTRIBUT',
                        'constat': 'ATTRIBUT_DIFFERENT',
                        'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
                        'systemes_a_corriger': targets,
                    }
                else:
                    labels = ', '.join(v.motif_final.libelle for v in validations)
                    history_comment = f'{len(validations)} cause(s) validée(s) : {labels}'
                    audit_values = {
                        'nb_causes': len(validations),
                        'causes': [
                            {
                                'motif': v.motif_final.code_motif,
                                'systeme_principal': v.systeme_a_corriger_final.code_systeme,
                                'prediction': v.prediction_retenue_id,
                            }
                            for v in validations
                        ],
                    }

                HistoriqueAnomalie.objects.create(
                    anomalie=anomaly,
                    ancien_statut=old_status,
                    nouveau_statut=Anomalie.Statut.VALIDEE,
                    source_evenement='SUPERVISEUR',
                    superviseur=request.user,
                    commentaire=history_comment,
                )
                _audit(request, 'VALIDATE', 'validation_motif', validations[0].pk, audit_values)

                try:
                    send_validation_email(validations[0])
                    if anomaly.niveau == Anomalie.Niveau.SERVICE:
                        messages.success(
                            request,
                            'Constat validé. Le système concerné a été notifié.',
                        )
                    elif is_attribute_difference:
                        targets = ', '.join(
                            v.systeme_a_corriger_final.code_systeme for v in validations
                        )
                        messages.success(
                            request,
                            f'Valeur différente validée. Système(s) à corriger : {targets}. '
                            'Les équipes correspondantes ont été notifiées.',
                        )
                    else:
                        messages.success(
                            request,
                            f'{len(validations)} cause(s) validée(s). Les équipes responsables ont été notifiées.',
                        )
                except Exception as exc:
                    prefix = 'Constat enregistré' if anomaly.niveau == Anomalie.Niveau.SERVICE else 'Décision enregistrée'
                    messages.warning(
                        request,
                        f'{prefix}, mais certaines notifications n’ont pas pu être envoyées : {exc}',
                    )
                return redirect('anomaly_detail', pk=pk)
    else:
        form = MultiCauseValidationForm(anomaly)

    return render(request, 'supervision/validation_form.html', {'form': form, 'anomaly': anomaly})
