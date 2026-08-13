from decimal import Decimal
from pathlib import Path
import joblib

from supervision.models import (
    AttributDefinition, EnvoiSnapshot, ExempleApprentissage, ModeleML, Motif,
    PredictionMotif, RegleMetier, ServiceAttributRegle, Systeme,
)
from .format_rules import evaluate_validation_rule
from .security import decrypt_sensitive


def _candidate_source_envois(rule, anomaly):
    links = anomaly.campagne.imports.select_related('systeme', 'fichier_import')
    if rule is not None and rule.flux_id:
        links = links.filter(systeme=rule.flux.systeme_source)
    else:
        present_system_ids = anomaly.details.filter(objet_present=True).values_list('systeme_id', flat=True)
        links = links.filter(systeme_id__in=present_system_ids)
    result = []
    for link in links:
        envoi = EnvoiSnapshot.objects.filter(
            fichier_import=link.fichier_import,
            code_envoi=anomaly.code_envoi,
        ).first()
        if envoi:
            result.append((link.systeme, envoi))
    return result


def _rule_values(rule, anomaly):
    if not rule.attribut_id:
        return []
    values = []
    for system, envoi in _candidate_source_envois(rule, anomaly):
        if rule.attribut.portee == AttributDefinition.Portee.ENVOI:
            value = envoi.valeurs_attribut.filter(attribut=rule.attribut).first()
            values.append((system, value))
        else:
            services = envoi.services.all()
            if anomaly.code_service:
                services = services.filter(code_service=anomaly.code_service)
            found = False
            for service in services:
                value = service.valeurs_attribut.filter(attribut=rule.attribut).first()
                values.append((system, value))
                found = True
            if not found:
                values.append((system, None))
    return values


def _structural_issues(anomaly, field):
    issues = []
    for system, shipment in _candidate_source_envois(None, anomaly):
        if field == 'NUM_COMMANDE' and not shipment.num_commande.strip():
            issues.append({
                'systeme': system.code_systeme,
                'champ': field,
                'constat': 'Champ vide ou absent dans la source',
            })
    return issues


def _rule_matches(rule, anomaly):
    expr = rule.expression_regle or {}
    if expr.get('type_ecart') and expr['type_ecart'] != anomaly.type_ecart:
        return False
    if rule.type_controle == 'STRUCTURE_SOURCE':
        return bool(_structural_issues(anomaly, expr.get('field', '')))
    if rule.attribut_id:
        values = _rule_values(rule, anomaly)
        if rule.type_controle == 'VIDE':
            return any(v is None or v.est_vide for _, v in values)
        if rule.type_controle == 'FORMAT_INCOMPATIBLE':
            return any(v is not None and v.format_source_conforme is False for _, v in values)
        if rule.type_controle == 'DOMAINE':
            allowed = set(expr.get('allowed', []))
            return any(
                v is not None and v.valeur_normalisee not in allowed
                for _, v in values if v.valeur_normalisee
            )
    details = list(anomaly.details.all())
    if rule.type_controle == 'VIDE':
        return any(not d.objet_present for d in details)
    if rule.type_controle == 'FORMAT_INCOMPATIBLE':
        return any(d.format_source_conforme is False for d in details)
    if rule.type_controle == 'DIFFERENCE':
        vals = {d.valeur_normalisee for d in details if d.valeur_normalisee not in (None, '')}
        return len(vals) > 1
    return bool(expr) or rule.type_controle == 'AUTRE'


def _diagnostic_evidence(rule, anomaly):
    evidence = []
    if rule.type_controle == 'STRUCTURE_SOURCE':
        return _structural_issues(anomaly, (rule.expression_regle or {}).get('field', ''))
    if rule.attribut_id:
        for system, value in _rule_values(rule, anomaly):
            if value is None or value.est_vide:
                evidence.append({
                    'systeme': system.code_systeme,
                    'champ': rule.attribut.code_attribut,
                    'constat': 'Champ vide ou absent dans la source',
                })
            elif value.format_source_conforme is False:
                evidence.append({
                    'systeme': system.code_systeme,
                    'champ': rule.attribut.code_attribut,
                    'constat': 'Format source non conforme',
                })
    elif rule.type_controle == 'VIDE':
        for detail in anomaly.details.select_related('systeme').all():
            if not detail.objet_present:
                evidence.append({
                    'systeme': detail.systeme.code_systeme,
                    'champ': anomaly.code_service or anomaly.niveau,
                    'constat': f'{anomaly.get_niveau_display()} absent dans ce système',
                })
    elif rule.type_controle == 'DIFFERENCE':
        for detail in anomaly.details.select_related('systeme').all():
            evidence.append({
                'systeme': detail.systeme.code_systeme,
                'champ': anomaly.attribut.code_attribut if anomaly.attribut_id else 'ATTRIBUT',
                'constat': f'Valeur observée : {detail.valeur_brute or "—"}',
            })
    return evidence


def _evidence_system_codes(evidence):
    issue_markers = ('vide', 'absent', 'non conforme', 'incompatible', 'aucun service')
    codes = []
    for item in evidence or []:
        constat = str(item.get('constat') or '').lower()
        code = str(item.get('systeme') or '').strip().upper()
        if code and any(marker in constat for marker in issue_markers) and code not in codes:
            codes.append(code)
    return codes


def _stored_target_codes(prediction):
    codes = []
    explanation = prediction.explication or {}
    for code in explanation.get('systemes_a_corriger') or []:
        normalized = str(code or '').strip().upper()
        if normalized and normalized not in codes:
            codes.append(normalized)
    if not codes and prediction.systeme_a_corriger_predit_id:
        codes.append(prediction.systeme_a_corriger_predit.code_systeme)
    return codes


def _correction_system_codes(rule, anomaly):
    if rule.flux_id:
        return [rule.flux.systeme_source.code_systeme]
    evidence = _diagnostic_evidence(rule, anomaly)
    codes = _evidence_system_codes(evidence)
    if codes:
        return codes
    if anomaly.systeme_ecart_id:
        return [anomaly.systeme_ecart.code_systeme]
    return []


def prediction_target_codes(prediction):
    explanation = prediction.explication or {}
    if explanation.get('diagnostic_dynamique') or explanation.get('role_diagnostic') == 'CONSTAT_SERVICE':
        evidence_codes = _evidence_system_codes(explanation.get('indices') or [])
        return evidence_codes or _stored_target_codes(prediction)
    if prediction.source_prediction == PredictionMotif.Source.REGLE and prediction.regle_id:
        rule = prediction.regle
        if _rule_matches(rule, prediction.anomalie):
            return _correction_system_codes(rule, prediction.anomalie)
        return []
    evidence_codes = _evidence_system_codes(explanation.get('indices') or [])
    if evidence_codes:
        return evidence_codes
    return _stored_target_codes(prediction)


def refresh_prediction_routing(anomaly):
    predictions = anomaly.predictions.select_related(
        'regle__flux__systeme_source', 'regle__flux__systeme_destination',
        'regle__attribut', 'systeme_a_corriger_predit',
    ).all()
    for prediction in predictions:
        codes = prediction_target_codes(prediction)
        target = Systeme.objects.filter(code_systeme=codes[0], actif=True).first() if codes else None
        explanation = dict(prediction.explication or {})
        stored_codes = [str(code or '').strip().upper() for code in explanation.get('systemes_a_corriger') or []]
        if stored_codes == codes and prediction.systeme_a_corriger_predit_id == (target.pk if target else None):
            continue
        explanation['systemes_a_corriger'] = codes
        prediction.explication = explanation
        prediction.systeme_a_corriger_predit = target
        prediction.save(update_fields=['explication', 'systeme_a_corriger_predit'])


def _learned_signature(anomaly):
    return {
        'niveau': anomaly.niveau,
        'type_ecart': anomaly.type_ecart,
        'service': anomaly.code_service or '',
        'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
        'systeme_ecart': anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
    }


def _append_learned_predictions(anomaly, rank, used_motif_ids):
    signature = _learned_signature(anomaly)
    examples = (
        ExempleApprentissage.objects.filter(eligible=True)
        .select_related('motif_label', 'systeme_a_corriger_label')
        .order_by('-cree_le')[:250]
    )
    seen = set()
    for example in examples:
        if rank > 3:
            break
        features = example.caracteristiques or {}
        if any(features.get(key, '') != value for key, value in signature.items()):
            continue
        if example.motif_label_id in used_motif_ids or example.motif_label_id in seen:
            continue
        PredictionMotif.objects.create(
            anomalie=anomaly,
            source_prediction=PredictionMotif.Source.APPRENTISSAGE,
            motif=example.motif_label,
            systeme_a_corriger_predit=example.systeme_a_corriger_label,
            rang=rank,
            score_confiance=Decimal('0.7500'),
            explication={
                'message': 'Motif reconnu à partir d’un dossier similaire validé par le superviseur.',
                'origine': f'Dossier #{example.validation.anomalie_id}',
                'systemes_a_corriger': [example.systeme_a_corriger_label.code_systeme],
                'role_diagnostic': 'SUGGESTION',
            },
        )
        seen.add(example.motif_label_id)
        rank += 1
    return rank


def _generic_rule(motif, level, code_suffix, type_control='AUTRE'):
    code = f'DYNAMIC_{level}_{code_suffix}'[:80]
    rule, _ = RegleMetier.objects.get_or_create(
        code_regle=code,
        defaults={
            'motif_suggere': motif,
            'niveau_anomalie': level,
            'type_controle': type_control,
            'expression_regle': {},
            'seuil_confiance': Decimal('0.8000'),
            'priorite': 9900,
            'actif': False,
        },
    )
    return rule


def _motif_for_empty_attribute(attribute):
    code = attribute.code_attribut.upper()
    if 'VILLE' in code:
        return Motif.objects.filter(code_motif='VILLE_MANQUANTE', actif=True).first()
    if 'TELEPHONE' in code:
        return Motif.objects.filter(code_motif='TELEPHONE_MANQUANT', actif=True).first()
    return Motif.objects.filter(code_motif='CHAMP_SOURCE_MANQUANT', actif=True).first()


def _motif_for_envoi_format(attribute):
    if 'TELEPHONE' in attribute.code_attribut.upper():
        return Motif.objects.filter(code_motif='FORMAT_TELEPHONE_INVALIDE', actif=True).first()
    return Motif.objects.filter(code_motif='FORMAT_SOURCE_INCOMPATIBLE', actif=True).first()


def _motif_for_attribute_format(attribute):
    if attribute.type_valeur == AttributDefinition.TypeValeur.NOMBRE:
        return Motif.objects.filter(code_motif='FORMAT_MONTANT_INCOMPATIBLE', actif=True).first()
    return Motif.objects.filter(code_motif='FORMAT_ATTRIBUT_INCOMPATIBLE', actif=True).first()


def _target_validation_rule(attribute, target_system, service_ref=None):
    if not target_system:
        return None
    rules = ServiceAttributRegle.objects.filter(
        attribut=attribute, systeme=target_system, actif=True,
    ).select_related('service_ref', 'systeme')
    if service_ref is not None:
        exact = rules.filter(service_ref=service_ref).first()
        if exact:
            return exact
    return rules.filter(service_ref__isnull=True).first()


def _plain_value(value):
    raw = value.valeur_brute or ''
    if value.attribut.sensible and raw:
        return decrypt_sensitive(raw)
    return raw


def _target_format_issue(value, target_rule):
    if not target_rule or not target_rule.regle_validation or value.est_vide:
        return None, ''
    return evaluate_validation_rule(_plain_value(value), value.attribut, target_rule.regle_validation)


def _create_dynamic_prediction(anomaly, rank, motif, evidence, score, field, message, role='CAUSE_ACTIVE'):
    if not motif or not evidence:
        return rank
    target_codes = _evidence_system_codes(evidence)
    if not target_codes:
        return rank
    predicted_system = Systeme.objects.filter(code_systeme=target_codes[0], actif=True).first()
    if not predicted_system:
        return rank
    rule = _generic_rule(motif, anomaly.niveau, motif.code_motif)
    PredictionMotif.objects.create(
        anomalie=anomaly,
        source_prediction=PredictionMotif.Source.REGLE,
        regle=rule,
        motif=motif,
        systeme_a_corriger_predit=predicted_system,
        rang=rank,
        score_confiance=score,
        explication={
            'diagnostic_dynamique': True,
            'attribut_analyse': field,
            'indices': evidence,
            'message': message,
            'systemes_a_corriger': target_codes,
            'role_diagnostic': role,
        },
    )
    return rank + 1


def _append_all_fields_envoi_diagnostics(anomaly, rank):
    if anomaly.niveau != anomaly.Niveau.ENVOI or anomaly.type_ecart != anomaly.TypeEcart.ABSENT:
        return rank
    target_system = anomaly.systeme_ecart
    grouped = {}

    def add_issue(kind, motif, field, system_code, constat, score):
        if not motif:
            return
        key = (kind, motif.pk, field, constat)
        bucket = grouped.setdefault(key, {'motif': motif, 'field': field, 'score': score, 'evidence': []})
        bucket['score'] = max(bucket['score'], score)
        bucket['evidence'].append({'systeme': system_code, 'champ': field, 'constat': constat})

    for system, shipment in _candidate_source_envois(None, anomaly):
        if not shipment.num_commande.strip():
            motif = Motif.objects.filter(code_motif='CHAMP_SOURCE_MANQUANT', actif=True).first()
            add_issue('EMPTY', motif, 'NUM_COMMANDE', system.code_systeme, 'Champ vide ou absent dans la source', Decimal('0.9000'))

        services = list(shipment.services.select_related('service_ref').prefetch_related('valeurs_attribut__attribut'))
        if not services:
            motif = Motif.objects.filter(code_motif='CHAMP_SOURCE_MANQUANT', actif=True).first()
            add_issue('EMPTY', motif, 'ARTICLE', system.code_systeme, 'Aucun service renseigné dans la source', Decimal('0.8000'))

        value_contexts = [(value, None) for value in shipment.valeurs_attribut.select_related('attribut').all()]
        for service in services:
            for value in service.valeurs_attribut.select_related('attribut').all():
                value_contexts.append((value, service.service_ref))

        for value, service_ref in value_contexts:
            attribute = value.attribut
            field = attribute.code_attribut
            target_rule = _target_validation_rule(attribute, target_system, service_ref)
            if value.est_vide:
                motif = _motif_for_empty_attribute(attribute)
                score = Decimal('0.9500') if target_rule and target_rule.obligatoire else Decimal('0.7000')
                label = 'Champ obligatoire vide dans la source' if target_rule and target_rule.obligatoire else 'Champ vide ou absent dans la source'
                add_issue('EMPTY', motif, field, system.code_systeme, label, score)
                continue
            if value.format_source_conforme is False:
                add_issue('FORMAT_SOURCE', _motif_for_envoi_format(attribute), field, system.code_systeme, 'Format source non conforme', Decimal('0.9000'))
            compatible, reason = _target_format_issue(value, target_rule)
            if compatible is False:
                target_code = target_system.code_systeme if target_system else 'le système cible'
                constat = f'Format potentiellement incompatible avec {target_code}'
                if reason:
                    constat += f' : {reason}'
                add_issue('FORMAT_CIBLE', _motif_for_envoi_format(attribute), field, system.code_systeme, constat, Decimal('0.9200'))

    for (_kind, _motif_id, field, _constat), item in grouped.items():
        systems = ', '.join(dict.fromkeys(index['systeme'] for index in item['evidence']))
        rank = _create_dynamic_prediction(
            anomaly, rank, item['motif'], item['evidence'], item['score'], field,
            f'{field} nécessite une vérification dans {systems}.',
        )
    return rank


def _attribute_source_values(anomaly):
    if not anomaly.attribut_id:
        return []
    values = []
    for system, shipment in _candidate_source_envois(None, anomaly):
        if anomaly.attribut.portee == AttributDefinition.Portee.ENVOI:
            value = shipment.valeurs_attribut.filter(attribut=anomaly.attribut).first()
            if value:
                values.append((system, value, None))
            continue
        services = shipment.services.all()
        if anomaly.code_service:
            services = services.filter(code_service=anomaly.code_service)
        for service in services.select_related('service_ref'):
            value = service.valeurs_attribut.filter(attribut=anomaly.attribut).first()
            if value:
                values.append((system, value, service.service_ref))
    return values


def _append_attribute_format_diagnostics(anomaly, rank):
    if anomaly.niveau != anomaly.Niveau.ATTRIBUT or not anomaly.attribut_id:
        return rank
    target_system = anomaly.systeme_ecart if anomaly.type_ecart == anomaly.TypeEcart.ABSENT else None
    evidence, reasons = [], []
    for system, value, service_ref in _attribute_source_values(anomaly):
        if value.est_vide:
            continue
        reason = ''
        incompatible = value.format_source_conforme is False
        if incompatible:
            reason = 'Format source non conforme'
        if target_system:
            target_rule = _target_validation_rule(anomaly.attribut, target_system, service_ref)
            compatible, target_reason = _target_format_issue(value, target_rule)
            if compatible is False:
                incompatible = True
                reason = f'Format potentiellement incompatible avec {target_system.code_systeme}'
                if target_reason:
                    reason += f' : {target_reason}'
        if incompatible:
            evidence.append({'systeme': system.code_systeme, 'champ': anomaly.attribut.code_attribut, 'constat': reason})
            if reason not in reasons:
                reasons.append(reason)
    if not evidence:
        return rank
    return _create_dynamic_prediction(
        anomaly, rank, _motif_for_attribute_format(anomaly.attribut), evidence,
        Decimal('0.9000'), anomaly.attribut.code_attribut, ' ; '.join(reasons),
    )


def _service_constat_rule():
    motif = Motif.objects.filter(code_motif='SERVICE_ABSENT', actif=True).first()
    if not motif:
        return None
    return _generic_rule(motif, 'SERVICE', 'SERVICE_SYNC', type_control='VIDE')


def _append_service_constat(anomaly, rank):
    if anomaly.niveau != anomaly.Niveau.SERVICE or anomaly.type_ecart != anomaly.TypeEcart.ABSENT:
        return rank
    rule = _service_constat_rule()
    if not rule:
        return rank
    missing_codes = [
        detail.systeme.code_systeme
        for detail in anomaly.details.select_related('systeme').all()
        if not detail.objet_present
    ]
    if not missing_codes:
        return rank
    target = Systeme.objects.filter(code_systeme=missing_codes[0], actif=True).first()
    if not target:
        return rank
    PredictionMotif.objects.create(
        anomalie=anomaly,
        source_prediction=PredictionMotif.Source.REGLE,
        regle=rule,
        motif=rule.motif_suggere,
        systeme_a_corriger_predit=target,
        rang=rank,
        score_confiance=Decimal('1.0000'),
        explication={
            'diagnostic_dynamique': True,
            'message': f'Service {anomaly.code_service} absent dans {", ".join(missing_codes)}.',
            'systemes_a_corriger': missing_codes,
            'role_diagnostic': 'CONSTAT_SERVICE',
            'afficher_motif': False,
        },
    )
    return rank + 1


def _append_rule_predictions(anomaly, rank, used_motif_ids):
    rules = RegleMetier.objects.filter(actif=True, niveau_anomalie=anomaly.niveau).select_related(
        'motif_suggere', 'flux__systeme_source', 'flux__systeme_destination', 'attribut'
    )
    for rule in rules:
        if not _rule_matches(rule, anomaly):
            continue
        target_codes = _correction_system_codes(rule, anomaly)
        predicted_system = Systeme.objects.filter(code_systeme=target_codes[0]).first() if target_codes else anomaly.systeme_ecart
        PredictionMotif.objects.create(
            anomalie=anomaly,
            source_prediction=PredictionMotif.Source.REGLE,
            regle=rule,
            motif=rule.motif_suggere,
            systeme_a_corriger_predit=predicted_system,
            rang=rank,
            score_confiance=rule.seuil_confiance,
            explication={
                'regle': rule.code_regle,
                'type_controle': rule.type_controle,
                'attribut_analyse': rule.attribut.code_attribut if rule.attribut_id else None,
                'indices': _diagnostic_evidence(rule, anomaly),
                'systemes_a_corriger': target_codes,
                'role_diagnostic': 'CAUSE_ACTIVE',
            },
        )
        used_motif_ids.add(rule.motif_suggere_id)
        rank += 1
    return rank


def analyze_anomaly(anomaly):
    PredictionMotif.objects.filter(anomalie=anomaly).delete()
    rank, used_motif_ids = 1, set()
    if anomaly.niveau == anomaly.Niveau.SERVICE:
        rank = _append_service_constat(anomaly, rank)
    elif anomaly.niveau == anomaly.Niveau.ENVOI:
        rank = _append_all_fields_envoi_diagnostics(anomaly, rank)
    else:
        rank = _append_attribute_format_diagnostics(anomaly, rank)
        rank = _append_rule_predictions(anomaly, rank, used_motif_ids)

    if rank == 1 and anomaly.niveau != anomaly.Niveau.SERVICE:
        rank = _append_learned_predictions(anomaly, rank, used_motif_ids)
        rank = _append_ml_predictions(anomaly, rank)

    if rank == 1:
        unknown = Motif.objects.filter(code_motif='MOTIF_INCONNU').first()
        fallback = _fallback_rule()
        if unknown and fallback:
            target_codes = [anomaly.systeme_ecart.code_systeme] if anomaly.systeme_ecart_id else []
            PredictionMotif.objects.create(
                anomalie=anomaly,
                source_prediction=PredictionMotif.Source.REGLE,
                regle=fallback,
                motif=unknown,
                systeme_a_corriger_predit=anomaly.systeme_ecart,
                rang=1,
                score_confiance=Decimal('0.1000'),
                explication={
                    'message': 'Aucune cause suffisamment étayée. Investigation nécessaire.',
                    'systemes_a_corriger': target_codes,
                    'role_diagnostic': 'SUGGESTION',
                },
            )
    if anomaly.predictions.exists() and anomaly.statut == anomaly.Statut.DETECTEE:
        anomaly.statut = anomaly.Statut.ANALYSEE
        anomaly.save(update_fields=['statut'])


def _fallback_rule():
    motif = Motif.objects.filter(code_motif='MOTIF_INCONNU').first()
    if not motif:
        return None
    rule, _ = RegleMetier.objects.get_or_create(
        code_regle='FALLBACK_INCONNU',
        defaults={
            'motif_suggere': motif,
            'niveau_anomalie': 'ENVOI',
            'type_controle': 'AUTRE',
            'expression_regle': {},
            'seuil_confiance': Decimal('0.1000'),
            'priorite': 9999,
            'actif': False,
        },
    )
    return rule


def _feature_dict(anomaly):
    return {
        'niveau': anomaly.niveau,
        'type_ecart': anomaly.type_ecart,
        'code_service': anomaly.code_service or '',
        'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
        'systeme_ecart': anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
        'nb_valeurs_vides': sum(1 for d in anomaly.details.all() if not d.objet_present),
        'nb_formats_invalides': sum(1 for d in anomaly.details.all() if d.format_source_conforme is False),
    }


def _append_ml_predictions(anomaly, rank):
    model_meta = ModeleML.objects.filter(actif=True).order_by('-entraine_le').first()
    if not model_meta or rank > 3:
        return rank
    path = Path(model_meta.chemin_fichier)
    if not path.exists():
        return rank
    try:
        pipeline = joblib.load(path)
        features = _feature_dict(anomaly)
        if not hasattr(pipeline, 'predict_proba'):
            return rank
        probabilities = pipeline.predict_proba([features])[0]
        classes = pipeline.classes_
        ordered = sorted(zip(classes, probabilities), key=lambda x: x[1], reverse=True)
        for code_motif, score in ordered[: max(0, 4-rank)]:
            motif = Motif.objects.filter(code_motif=str(code_motif), actif=True).first()
            if not motif:
                continue
            target_codes = [anomaly.systeme_ecart.code_systeme] if anomaly.systeme_ecart_id else []
            PredictionMotif.objects.create(
                anomalie=anomaly,
                source_prediction=PredictionMotif.Source.ML,
                modele=model_meta,
                motif=motif,
                systeme_a_corriger_predit=anomaly.systeme_ecart,
                rang=rank,
                score_confiance=Decimal(str(round(float(score), 4))),
                explication={
                    'features': features,
                    'systemes_a_corriger': target_codes,
                    'role_diagnostic': 'SUGGESTION',
                },
            )
            rank += 1
        return rank
    except Exception:
        return rank
