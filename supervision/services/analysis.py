from decimal import Decimal
from pathlib import Path
import joblib

from supervision.models import (
    AttributDefinition, EnvoiSnapshot, ExempleApprentissage, ModeleML, Motif,
    PredictionMotif, RegleMetier, Systeme,
)


def _candidate_source_envois(rule, anomaly):
    links = anomaly.campagne.imports.select_related('systeme', 'fichier_import')
    if rule is not None and rule.flux_id:
        links = links.filter(systeme=rule.flux.systeme_source)
    else:
        present_system_ids = anomaly.details.filter(objet_present=True).values_list('systeme_id', flat=True)
        links = links.filter(systeme_id__in=present_system_ids)
    result = []
    for link in links:
        envoi = EnvoiSnapshot.objects.filter(fichier_import=link.fichier_import, code_envoi=anomaly.code_envoi).first()
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
            issues.append({'systeme': system.code_systeme, 'champ': field, 'constat': 'Champ obligatoire vide'})
        if field in {'ARTICLE', 'DES_ARTICLE'}:
            services = list(shipment.services.all())
            if field == 'ARTICLE' and not services:
                issues.append({'systeme': system.code_systeme, 'champ': field, 'constat': 'Aucun service renseigné'})
            for service in services:
                value = service.code_service if field == 'ARTICLE' else service.libelle_service
                if value and value.strip().isdigit():
                    issues.append({
                        'systeme': system.code_systeme,
                        'champ': field,
                        'constat': 'Le champ contient uniquement des chiffres',
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
            return any(v is not None and v.valeur_normalisee not in allowed for _, v in values if v.valeur_normalisee)
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
    """Extract systems that actually contain a source-side issue from diagnostic evidence."""
    issue_markers = (
        'vide', 'absent', 'non conforme', 'aucun service', 'uniquement des chiffres',
    )
    codes = []
    for item in evidence or []:
        constat = str(item.get('constat') or '').lower()
        code = str(item.get('systeme') or '').strip().upper()
        if code and any(marker in constat for marker in issue_markers) and code not in codes:
            codes.append(code)
    return codes


def _correction_system_codes(rule, anomaly):
    """Return every system that contains the detected cause, without hard-coded system names."""
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
    """Resolve responsible systems from current evidence, not only persisted prediction metadata.

    Rule-based predictions are recomputed against the campaign snapshots. This protects
    older dossiers created before routing fixes: the observed system is never trusted as
    the correction target when source evidence identifies other systems.
    """
    if prediction.source_prediction == PredictionMotif.Source.REGLE and prediction.regle_id:
        rule = prediction.regle
        if _rule_matches(rule, prediction.anomalie):
            return _correction_system_codes(rule, prediction.anomalie)
        return []

    explanation = prediction.explication or {}
    evidence_codes = _evidence_system_codes(explanation.get('indices') or [])
    if evidence_codes:
        return evidence_codes

    codes = []
    for code in explanation.get('systemes_a_corriger') or []:
        normalized = str(code or '').strip().upper()
        if normalized and normalized not in codes:
            codes.append(normalized)
    if not codes and prediction.systeme_a_corriger_predit_id:
        codes.append(prediction.systeme_a_corriger_predit.code_systeme)
    return codes


def refresh_prediction_routing(anomaly):
    """Refresh persisted routing metadata without deleting predictions or validation history."""
    predictions = anomaly.predictions.select_related(
        'regle__flux__systeme_source', 'regle__flux__systeme_destination',
        'regle__attribut', 'systeme_a_corriger_predit',
    ).all()
    for prediction in predictions:
        if prediction.source_prediction != PredictionMotif.Source.REGLE or not prediction.regle_id:
            continue
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
    """Reuse validated human decisions before relying on an external ML model."""
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


def _service_absence_fallback_rule():
    """Inactive structural rule used only after source-side rules found no concrete cause."""
    motif = Motif.objects.filter(code_motif='SERVICE_ABSENT', actif=True).first()
    if not motif:
        return None
    rule, _ = RegleMetier.objects.get_or_create(
        code_regle='FALLBACK_SERVICE_ABSENT',
        defaults={
            'motif_suggere': motif,
            'niveau_anomalie': 'SERVICE',
            'type_controle': 'VIDE',
            'expression_regle': {'type_ecart': 'ABSENT'},
            'seuil_confiance': Decimal('0.9800'),
            'priorite': 9900,
            'actif': False,
        },
    )
    return rule


def _append_service_absence_prediction(anomaly, rank):
    """Diagnose a missing service only when no stronger source-side cause was found."""
    if rank != 1 or anomaly.niveau != anomaly.Niveau.SERVICE or anomaly.type_ecart != anomaly.TypeEcart.ABSENT:
        return rank

    rule = _service_absence_fallback_rule()
    if not rule or not _rule_matches(rule, anomaly):
        return rank

    evidence = _diagnostic_evidence(rule, anomaly)
    target_codes = _evidence_system_codes(evidence)
    if not target_codes:
        return rank

    present_codes = list(
        anomaly.details.filter(objet_present=True)
        .select_related('systeme')
        .order_by('systeme__ordre_comparaison')
        .values_list('systeme__code_systeme', flat=True)
    )
    predicted_system = Systeme.objects.filter(code_systeme=target_codes[0], actif=True).first()
    if predicted_system is None:
        return rank

    service_label = anomaly.code_service or 'concerné'
    present_text = ', '.join(present_codes) if present_codes else 'les autres systèmes'
    missing_text = ', '.join(target_codes)
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
            'indices': evidence,
            'message': (
                f'Le service {service_label} est présent dans {present_text} '
                f'et absent dans {missing_text}. Aucun autre défaut source prioritaire n’a été identifié.'
            ),
            'systemes_a_corriger': target_codes,
            'role_diagnostic': 'CAUSE_ACTIVE',
        },
    )
    return rank + 1


def analyze_anomaly(anomaly):
    PredictionMotif.objects.filter(anomalie=anomaly).delete()
    rank = 1
    used_motif_ids = set()
    rules = RegleMetier.objects.filter(
        actif=True,
        niveau_anomalie=anomaly.niveau,
    ).select_related(
        'motif_suggere', 'flux__systeme_source', 'flux__systeme_destination', 'attribut'
    )

    # Toutes les règles métier effectivement violées sont conservées. Elles ne
    # sont pas des alternatives entre elles : plusieurs causes peuvent expliquer
    # simultanément le même blocage de synchronisation.
    for rule in rules:
        if _rule_matches(rule, anomaly):
            target_codes = _correction_system_codes(rule, anomaly)
            predicted_system = None
            if target_codes:
                predicted_system = Systeme.objects.filter(code_systeme=target_codes[0]).first()
            if predicted_system is None:
                predicted_system = anomaly.systeme_ecart
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

    # Si aucun défaut source plus précis n'explique l'écart, un service réellement
    # présent dans les autres systèmes et absent dans un système devient lui-même
    # une cause de synchronisation déterministe.
    if rank == 1:
        rank = _append_service_absence_prediction(anomaly, rank)

    # L'apprentissage/ML reste un mécanisme de suggestion quand aucune règle
    # explicite n'a identifié de cause concrète dans les données.
    if rank == 1:
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
                    'message': 'Aucune règle ou prédiction ML suffisamment précise.',
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