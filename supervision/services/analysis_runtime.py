from collections import defaultdict
from decimal import Decimal

from supervision.models import (
    Anomalie, AttributDefinition, FluxSynchronisation, Motif, PredictionMotif,
    RegleMetier, ServiceAttributRegle, Systeme,
)

from .analysis import (
    _candidate_source_envois,
    _generic_rule,
    _target_format_issue,
    _target_validation_rule,
    analyze_anomaly as _base_analyze_anomaly,
)


def _unique_codes(items):
    result = []
    for item in items:
        code = str(item or '').strip().upper()
        if code and code not in result:
            result.append(code)
    return result


def _create_envoi_prediction(
    anomaly,
    rank,
    motif,
    field,
    message,
    evidence,
    score,
    role,
    source_codes=None,
):
    if motif is None:
        return rank
    source_codes = _unique_codes(
        source_codes if source_codes is not None
        else [item.get('systeme') for item in evidence]
    )
    target = Systeme.objects.filter(
        code_systeme=source_codes[0], actif=True
    ).first() if source_codes else None
    rule = _generic_rule(motif, Anomalie.Niveau.ENVOI, motif.code_motif)
    PredictionMotif.objects.create(
        anomalie=anomaly,
        source_prediction=PredictionMotif.Source.REGLE,
        regle=rule,
        motif=motif,
        systeme_a_corriger_predit=target,
        rang=rank,
        score_confiance=score,
        explication={
            'diagnostic_dynamique': True,
            'attribut_analyse': field,
            'indices': evidence,
            'message': message,
            'systemes_a_corriger': source_codes,
            'role_diagnostic': role,
        },
    )
    return rank + 1


def _source_shipments(anomaly):
    return _candidate_source_envois(None, anomaly)


def _required_target_rules(anomaly):
    if not anomaly.systeme_ecart_id:
        return ServiceAttributRegle.objects.none()
    return ServiceAttributRegle.objects.filter(
        systeme=anomaly.systeme_ecart,
        obligatoire=True,
        actif=True,
        attribut__actif=True,
    ).select_related('attribut', 'service_ref', 'systeme')


def _append_issue(bucket, field, system_code, constat, service_code=''):
    key = (str(system_code).upper(), str(field).upper(), str(service_code or ''))
    if key in bucket[field]:
        return
    bucket[field][key] = {
        'systeme': str(system_code).upper(),
        'champ': field,
        'service': service_code or '',
        'constat': constat,
    }


def _mandatory_missing_issues(anomaly):
    """N1 case 1: inspect every configured mandatory field in source systems."""
    issues = defaultdict(dict)
    rules = list(_required_target_rules(anomaly))

    for system, shipment in _source_shipments(anomaly):
        services_all = list(shipment.services.select_related('service_ref').all())
        if not services_all:
            _append_issue(
                issues,
                'ARTICLE',
                system.code_systeme,
                'Le champ ARTICLE est obligatoire et absent',
            )

        for rule in rules:
            attribute = rule.attribut
            field = attribute.code_attribut
            if attribute.portee == AttributDefinition.Portee.ENVOI:
                value = shipment.valeurs_attribut.filter(attribut=attribute).first()
                if value is None or value.est_vide:
                    _append_issue(
                        issues,
                        field,
                        system.code_systeme,
                        f'Le champ {field} est obligatoire et absent',
                    )
                continue

            services = services_all
            if rule.service_ref_id:
                services = [
                    service for service in services_all
                    if service.service_ref_id == rule.service_ref_id
                    or service.code_service == rule.service_ref.code_service
                ]
            for service in services:
                value = service.valeurs_attribut.filter(attribut=attribute).first()
                if value is None or value.est_vide:
                    _append_issue(
                        issues,
                        field,
                        system.code_systeme,
                        f'Le champ {field} est obligatoire et absent',
                        service.code_service,
                    )

    return {
        field: list(entries.values())
        for field, entries in issues.items()
        if entries
    }


def _format_issues(anomaly):
    """Legacy helper kept for compatibility; N1 no longer creates a format motif."""
    issues = defaultdict(dict)
    target_system = anomaly.systeme_ecart

    def inspect_value(system, value, service_ref=None, service_code=''):
        if value is None or value.est_vide:
            return
        field = value.attribut.code_attribut
        reasons = []

        if value.format_source_conforme is False:
            reasons.append('Format source non conforme')

        target_rule = _target_validation_rule(
            value.attribut, target_system, service_ref
        ) if target_system else None
        compatible, reason = _target_format_issue(value, target_rule)
        if compatible is False:
            target_code = target_system.code_systeme if target_system else 'le système cible'
            detail = f'Format incompatible avec {target_code}'
            if reason:
                detail += f' : {reason}'
            reasons.append(detail)

        if reasons:
            _append_issue(
                issues,
                field,
                system.code_systeme,
                ' ; '.join(dict.fromkeys(reasons)),
                service_code,
            )

    for system, shipment in _source_shipments(anomaly):
        for value in shipment.valeurs_attribut.select_related('attribut').all():
            inspect_value(system, value)
        for service in shipment.services.select_related('service_ref').all():
            for value in service.valeurs_attribut.select_related('attribut').all():
                inspect_value(
                    system,
                    value,
                    service.service_ref,
                    service.code_service,
                )

    return {
        field: list(entries.values())
        for field, entries in issues.items()
        if entries
    }


def _resolve_source_system(anomaly):
    """Resolve a unique source without guessing; otherwise require supervisor input."""
    present = [
        detail.systeme
        for detail in anomaly.details.select_related('systeme').all()
        if detail.objet_present
    ]
    present_codes = _unique_codes(system.code_systeme for system in present)
    if len(present_codes) == 1:
        return Systeme.objects.filter(code_systeme=present_codes[0], actif=True).first(), present_codes

    if anomaly.systeme_ecart_id and present_codes:
        configured_sources = _unique_codes(
            FluxSynchronisation.objects.filter(
                actif=True,
                systeme_destination=anomaly.systeme_ecart,
                systeme_source__code_systeme__in=present_codes,
            ).values_list('systeme_source__code_systeme', flat=True)
        )
        if len(configured_sources) == 1:
            source = Systeme.objects.filter(
                code_systeme=configured_sources[0], actif=True
            ).first()
            return source, present_codes

    return None, present_codes


def _append_unknown_envoi_prediction(anomaly):
    motif = Motif.objects.filter(code_motif='MOTIF_INCONNU', actif=True).first()
    if motif is None:
        return
    source, candidates = _resolve_source_system(anomaly)
    source_codes = [source.code_systeme] if source else []
    rule = _generic_rule(motif, Anomalie.Niveau.ENVOI, 'MOTIF_NON_IDENTIFIABLE')
    PredictionMotif.objects.create(
        anomalie=anomaly,
        source_prediction=PredictionMotif.Source.REGLE,
        regle=rule,
        motif=motif,
        systeme_a_corriger_predit=source,
        rang=1,
        score_confiance=Decimal('1.0000'),
        explication={
            'diagnostic_dynamique': True,
            'attribut_analyse': '',
            'indices': [],
            'message': 'Motif non identifiable',
            'systemes_a_corriger': source_codes,
            'systemes_sources_candidates': candidates,
            'systeme_source_a_confirmer': source is None,
            'role_diagnostic': 'MOTIF_NON_IDENTIFIABLE',
            'action_apres_validation': 'RESSAISIR_ENVOI_SOURCE',
        },
    )


def _analyze_envoi_strict(anomaly):
    """N1 has exactly two business outcomes: mandatory field missing, otherwise unknown."""
    PredictionMotif.objects.filter(anomalie=anomaly).delete()

    mandatory = _mandatory_missing_issues(anomaly)
    if mandatory:
        motif = Motif.objects.filter(
            code_motif='CHAMP_OBLIGATOIRE_ENVOI_ABSENT', actif=True
        ).first()
        rank = 1
        for field in sorted(mandatory):
            evidence = mandatory[field]
            rank = _create_envoi_prediction(
                anomaly,
                rank,
                motif,
                field,
                f'Le champ {field} est obligatoire et absent',
                evidence,
                Decimal('1.0000'),
                'CHAMP_OBLIGATOIRE_ABSENT',
            )
    else:
        _append_unknown_envoi_prediction(anomaly)

    if anomaly.predictions.exists() and anomaly.statut == anomaly.Statut.DETECTEE:
        anomaly.statut = anomaly.Statut.ANALYSEE
        anomaly.save(update_fields=['statut'])


def _attribute_prediction_is_relevant(prediction, anomaly):
    """Reject any diagnosis that is not about the attribute currently compared."""
    if anomaly.niveau != Anomalie.Niveau.ATTRIBUT or not anomaly.attribut_id:
        return True

    explanation = prediction.explication or {}
    analysed_field = str(explanation.get('attribut_analyse') or '').strip().upper()
    anomaly_field = anomaly.attribut.code_attribut.strip().upper()

    if analysed_field and analysed_field != anomaly_field:
        return False

    if prediction.regle_id and prediction.regle.attribut_id:
        if prediction.regle.attribut_id != anomaly.attribut_id:
            return False

    motif_level = str(prediction.motif.niveau_applicable or '').strip().upper()
    if motif_level and motif_level != Anomalie.Niveau.ATTRIBUT:
        return False

    if anomaly.type_ecart == Anomalie.TypeEcart.ABSENT:
        if prediction.source_prediction != PredictionMotif.Source.REGLE:
            return False
        if explanation.get('role_diagnostic') == 'SUGGESTION':
            return False

    return True


def _attribute_sync_rule(motif):
    rule, _ = RegleMetier.objects.get_or_create(
        code_regle='DYNAMIC_ATTRIBUT_NON_SYNCHRONISE',
        defaults={
            'motif_suggere': motif,
            'niveau_anomalie': Anomalie.Niveau.ATTRIBUT,
            'type_controle': 'AUTRE',
            'expression_regle': {},
            'seuil_confiance': Decimal('1.0000'),
            'priorite': 9990,
            'actif': False,
        },
    )
    return rule


def _attribute_difference_rule(motif):
    rule, _ = RegleMetier.objects.get_or_create(
        code_regle='DYNAMIC_ATTRIBUT_DIFFERENT_A_CONFIRMER',
        defaults={
            'motif_suggere': motif,
            'niveau_anomalie': Anomalie.Niveau.ATTRIBUT,
            'type_controle': 'DIFFERENCE',
            'expression_regle': {'type_ecart': Anomalie.TypeEcart.DIFFERENT},
            'seuil_confiance': Decimal('1.0000'),
            'priorite': 9991,
            'actif': False,
        },
    )
    return rule


def _append_attribute_sync_constat(anomaly):
    motif = Motif.objects.filter(
        code_motif='ATTRIBUT_NON_SYNCHRONISE', actif=True
    ).first()
    if motif is None:
        return

    target = anomaly.systeme_ecart if anomaly.type_ecart == Anomalie.TypeEcart.ABSENT else None
    target_codes = [target.code_systeme] if target else []
    evidence = []
    if target:
        evidence.append({
            'systeme': target.code_systeme,
            'champ': anomaly.attribut.code_attribut if anomaly.attribut_id else 'ATTRIBUT',
            'constat': 'Attribut absent / non synchronisé dans ce système',
        })

    PredictionMotif.objects.create(
        anomalie=anomaly,
        source_prediction=PredictionMotif.Source.REGLE,
        regle=_attribute_sync_rule(motif),
        motif=motif,
        systeme_a_corriger_predit=target,
        rang=1,
        score_confiance=Decimal('1.0000'),
        explication={
            'diagnostic_dynamique': True,
            'attribut_analyse': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
            'indices': evidence,
            'message': 'Attribut détecté comme non synchronisé entre les systèmes.',
            'systemes_a_corriger': target_codes,
            'role_diagnostic': 'CONSTAT_ATTRIBUT',
        },
    )


def _append_attribute_difference_constat(anomaly):
    motif = Motif.objects.filter(code_motif='ATTRIBUT_DIFFERENT', actif=True).first()
    if motif is None:
        return
    evidence = []
    for detail in anomaly.details.select_related('systeme').order_by('systeme__ordre_comparaison'):
        evidence.append({
            'systeme': detail.systeme.code_systeme,
            'champ': anomaly.attribut.code_attribut if anomaly.attribut_id else 'ATTRIBUT',
            'constat': f'Valeur observée : {detail.valeur_brute or "—"}',
        })
    PredictionMotif.objects.create(
        anomalie=anomaly,
        source_prediction=PredictionMotif.Source.REGLE,
        regle=_attribute_difference_rule(motif),
        motif=motif,
        systeme_a_corriger_predit=None,
        rang=1,
        score_confiance=Decimal('1.0000'),
        explication={
            'diagnostic_dynamique': True,
            'attribut_analyse': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
            'indices': evidence,
            'message': 'Valeurs différentes entre les systèmes — système à corriger à confirmer par le superviseur.',
            'systemes_a_corriger': [],
            'role_diagnostic': 'CONSTAT_ATTRIBUT_DIFFERENT',
            'systeme_a_corriger_a_confirmer': True,
        },
    )


def _has_attribute_format_cause(anomaly):
    for prediction in anomaly.predictions.select_related('motif').all():
        if prediction.motif.code_motif in {
            'FORMAT_MONTANT_INCOMPATIBLE',
            'FORMAT_ATTRIBUT_INCOMPATIBLE',
        }:
            return True
    return False


def analyze_anomaly(anomaly):
    """Apply the two N1 outcomes and deterministic N2/N3 routing rules."""
    if anomaly.niveau == Anomalie.Niveau.ENVOI:
        _analyze_envoi_strict(anomaly)
        return

    _base_analyze_anomaly(anomaly)

    if anomaly.niveau != Anomalie.Niveau.ATTRIBUT:
        return

    predictions = list(
        anomaly.predictions.select_related('motif', 'regle__attribut').order_by('rang')
    )
    invalid_ids = [
        prediction.pk
        for prediction in predictions
        if not _attribute_prediction_is_relevant(prediction, anomaly)
    ]
    if invalid_ids:
        anomaly.predictions.filter(pk__in=invalid_ids).delete()

    # A real format defect has priority: the source carrying the invalid format
    # is the system to correct after supervisor validation.
    if _has_attribute_format_cause(anomaly):
        non_format_ids = [
            prediction.pk
            for prediction in anomaly.predictions.select_related('motif').all()
            if prediction.motif.code_motif not in {
                'FORMAT_MONTANT_INCOMPATIBLE',
                'FORMAT_ATTRIBUT_INCOMPATIBLE',
            }
        ]
        if non_format_ids:
            anomaly.predictions.filter(pk__in=non_format_ids).delete()
    elif anomaly.type_ecart == Anomalie.TypeEcart.DIFFERENT:
        # No business rule has yet been approved to decide which different
        # value is authoritative. Never infer that the minority value is wrong.
        anomaly.predictions.all().delete()
        _append_attribute_difference_constat(anomaly)
    elif not anomaly.predictions.exists():
        # ABSENT without a demonstrated format cause: notify the system where
        # the attribute is missing, after supervisor validation.
        _append_attribute_sync_constat(anomaly)

    for rank, prediction in enumerate(anomaly.predictions.order_by('rang', 'pk'), start=1):
        if prediction.rang != rank:
            prediction.rang = rank
            prediction.save(update_fields=['rang'])
