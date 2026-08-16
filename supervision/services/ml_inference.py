from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import joblib
from django.db.models import Max

from supervision.models import Anomalie, ModeleML, Motif, PredictionMotif

from .ml_policy import MIN_CAUSE_SUPPORT, NON_CAUSAL_MOTIF_CODES, confidence_band


logger = logging.getLogger(__name__)


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


def _motif_is_compatible(anomaly, motif):
    code = str(motif.code_motif or '').strip().upper()
    if code in NON_CAUSAL_MOTIF_CODES:
        return False
    motif_level = str(motif.niveau_applicable or '').strip().upper()
    anomaly_level = str(anomaly.niveau or '').strip().upper()
    if motif_level and motif_level != anomaly_level:
        return False
    return True


def _existing_non_ml_motif_codes(anomaly):
    return set(
        anomaly.predictions.exclude(source_prediction=PredictionMotif.Source.ML)
        .values_list('motif__code_motif', flat=True)
    )


def _class_support(model, code_motif):
    metrics = model.metriques or {}
    counts = metrics.get('class_counts') or {}
    try:
        return int(counts.get(str(code_motif), 0) or 0)
    except (TypeError, ValueError):
        return 0


def ensure_ml_predictions(anomaly, max_suggestions=2):
    """Ajoute uniquement des causes ML compatibles et suffisamment étayées."""
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return []

    model = ModeleML.objects.filter(actif=True).order_by('-entraine_le').first()
    if model is None:
        from .ml_training import safe_maybe_retrain_model
        model = safe_maybe_retrain_model()
        if model is None:
            model = ModeleML.objects.filter(actif=True).order_by('-entraine_le').first()
    if model is None:
        return []

    deterministic_codes = _existing_non_ml_motif_codes(anomaly)
    existing = list(
        anomaly.predictions.filter(
            source_prediction=PredictionMotif.Source.ML,
            modele=model,
        ).select_related('motif').order_by('rang')
    )
    compatible_existing = [
        p for p in existing
        if _motif_is_compatible(anomaly, p.motif)
        and p.motif.code_motif not in deterministic_codes
        and _class_support(model, p.motif.code_motif) >= MIN_CAUSE_SUPPORT
    ]
    incompatible_ids = [p.pk for p in existing if p not in compatible_existing]
    if incompatible_ids:
        anomaly.predictions.filter(pk__in=incompatible_ids).delete()
    if compatible_existing:
        return compatible_existing[:max_suggestions]

    path = Path(model.chemin_fichier)
    if not path.exists():
        logger.warning('Modèle ML actif introuvable sur disque : %s', path)
        return []

    try:
        pipeline = joblib.load(path)
        if not hasattr(pipeline, 'predict_proba'):
            logger.warning('Le modèle ML actif ne fournit pas predict_proba().')
            return []

        features = _feature_dict(anomaly)
        probabilities = pipeline.predict_proba([features])[0]
        classes = pipeline.classes_
        ordered = sorted(zip(classes, probabilities), key=lambda item: item[1], reverse=True)

        last_rank = anomaly.predictions.aggregate(value=Max('rang'))['value'] or 0
        created = []
        for code_motif, probability in ordered:
            if len(created) >= max_suggestions:
                break
            code_motif = str(code_motif)
            if code_motif in deterministic_codes:
                continue
            motif = Motif.objects.filter(code_motif=code_motif, actif=True).first()
            if motif is None or not _motif_is_compatible(anomaly, motif):
                continue

            support = _class_support(model, code_motif)
            if support < MIN_CAUSE_SUPPORT:
                continue

            probability_value = float(probability)
            last_rank += 1
            created.append(
                PredictionMotif.objects.create(
                    anomalie=anomaly,
                    source_prediction=PredictionMotif.Source.ML,
                    modele=model,
                    motif=motif,
                    systeme_a_corriger_predit=None,
                    rang=last_rank,
                    score_confiance=Decimal(str(round(probability_value, 4))),
                    explication={
                        'message': motif.libelle,
                        'features': features,
                        'systemes_a_corriger': [],
                        'systeme_a_corriger_a_confirmer': True,
                        'role_diagnostic': 'SUGGESTION_CAUSE_ML',
                        'origine': 'MODELE_ML_ACTIF',
                        'version_modele': model.version_modele,
                        'niveau_confiance': confidence_band(probability_value),
                        'nb_exemples_cause': support,
                        'support_minimum_requis': MIN_CAUSE_SUPPORT,
                    },
                )
            )
        return created
    except Exception:
        logger.exception('Impossible de produire les suggestions ML pour l’anomalie #%s.', anomaly.pk)
        return []
