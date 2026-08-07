from decimal import Decimal
from pathlib import Path
import joblib

from supervision.models import (
    AttributDefinition, EnvoiSnapshot, ModeleML, Motif, PredictionMotif,
    RegleMetier, ServiceSnapshot, ValeurAttributSnapshot,
)


def _candidate_source_envois(rule, anomaly):
    links = anomaly.campagne.imports.select_related('systeme', 'fichier_import')
    if rule.flux_id:
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


def _rule_matches(rule, anomaly):
    expr = rule.expression_regle or {}
    if expr.get('type_ecart') and expr['type_ecart'] != anomaly.type_ecart:
        return False
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


def analyze_anomaly(anomaly):
    PredictionMotif.objects.filter(anomalie=anomaly).delete()
    rank = 1
    rules = RegleMetier.objects.filter(actif=True, niveau_anomalie=anomaly.niveau).select_related('motif_suggere', 'flux__systeme_source', 'flux__systeme_destination', 'attribut')
    for rule in rules:
        if rank > 3:
            break
        if _rule_matches(rule, anomaly):
            predicted_system = anomaly.systeme_ecart
            if rule.flux_id:
                predicted_system = rule.flux.systeme_source
            elif rule.attribut_id:
                matching = [system for system, value in _rule_values(rule, anomaly) if value is None or value.est_vide or value.format_source_conforme is False]
                if len(matching) == 1:
                    predicted_system = matching[0]
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
                },
            )
            rank += 1
    rank = _append_ml_predictions(anomaly, rank)
    if rank == 1:
        unknown = Motif.objects.filter(code_motif='MOTIF_INCONNU').first()
        fallback = _fallback_rule()
        if unknown and fallback:
            PredictionMotif.objects.create(
                anomalie=anomaly,
                source_prediction=PredictionMotif.Source.REGLE,
                regle=fallback,
                motif=unknown,
                systeme_a_corriger_predit=anomaly.systeme_ecart,
                rang=1,
                score_confiance=Decimal('0.1000'),
                explication={'message': 'Aucune règle ou prédiction ML suffisamment précise.'},
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
            PredictionMotif.objects.create(
                anomalie=anomaly,
                source_prediction=PredictionMotif.Source.ML,
                modele=model_meta,
                motif=motif,
                systeme_a_corriger_predit=anomaly.systeme_ecart,
                rang=rank,
                score_confiance=Decimal(str(round(float(score), 4))),
                explication={'features': features},
            )
            rank += 1
        return rank
    except Exception:
        return rank
