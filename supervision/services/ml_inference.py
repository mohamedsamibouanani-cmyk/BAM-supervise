from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import joblib
from django.db.models import Max

from supervision.models import Anomalie, ModeleML, Motif, PredictionMotif


logger = logging.getLogger(__name__)

# Ces libellés décrivent le constat technique lui-même, pas une cause métier.
# Le ML ne doit donc jamais les reproposer comme s'il avait découvert une cause.
_NON_CAUSAL_MOTIF_CODES = {
    'ATTRIBUT_NON_SYNCHRONISE',
    'ATTRIBUT_DIFFERENT',
    'SERVICE_ABSENT',
    'MOTIF_INCONNU',
}


def _feature_dict(anomaly):
    return {
        'niveau': anomaly.niveau,
        'type_ecart': anomaly.type_ecart,
        'code_service': anomaly.code_service or '',
        'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
        'systeme_ecart': anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
        'nb_valeurs_vides': sum(1 for d in anomaly.details.all() if not d.objet_present),
        'nb_formats_invalides': sum(
            1 for d in anomaly.details.all() if d.format_source_conforme is False
        ),
    }


def _motif_is_compatible(anomaly, motif):
    """N'autorise que de vraies causes compatibles avec l'anomalie courante.

    Le constat déterministe (envoi/service/valeur absente ou différente) reste
    séparé de l'aide ML. Le modèle doit proposer une cause apprise, jamais répéter
    le type d'écart déjà observé par le moteur de comparaison.
    """
    code = str(motif.code_motif or '').strip().upper()
    if code in _NON_CAUSAL_MOTIF_CODES:
        return False

    motif_level = str(motif.niveau_applicable or '').strip().upper()
    anomaly_level = str(anomaly.niveau or '').strip().upper()
    if motif_level and motif_level != anomaly_level:
        return False

    return True


def ensure_ml_predictions(anomaly, max_suggestions=2):
    """Ajoute des suggestions causales du modèle actif.

    Les règles métier et les cas appris restent des sources séparées. Seules les
    lignes ``source_prediction=ML`` portent une probabilité affichable. Le ML ne
    choisit jamais le système à corriger : cette décision appartient au superviseur.
    """
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

    existing = list(
        anomaly.predictions.filter(
            source_prediction=PredictionMotif.Source.ML,
            modele=model,
        ).select_related('motif').order_by('rang')
    )
    compatible_existing = [p for p in existing if _motif_is_compatible(anomaly, p.motif)]
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
        ordered = sorted(
            zip(classes, probabilities),
            key=lambda item: item[1],
            reverse=True,
        )

        last_rank = anomaly.predictions.aggregate(value=Max('rang'))['value'] or 0
        created = []
        for code_motif, probability in ordered:
            if len(created) >= max_suggestions:
                break
            motif = Motif.objects.filter(
                code_motif=str(code_motif), actif=True
            ).first()
            if motif is None or not _motif_is_compatible(anomaly, motif):
                continue

            last_rank += 1
            created.append(
                PredictionMotif.objects.create(
                    anomalie=anomaly,
                    source_prediction=PredictionMotif.Source.ML,
                    modele=model,
                    motif=motif,
                    # Le système observé n'est pas forcément le système responsable.
                    # Le ML suggère la cause uniquement ; le superviseur choisit la cible.
                    systeme_a_corriger_predit=None,
                    rang=last_rank,
                    score_confiance=Decimal(str(round(float(probability), 4))),
                    explication={
                        'message': motif.libelle,
                        'features': features,
                        'systemes_a_corriger': [],
                        'systeme_a_corriger_a_confirmer': True,
                        'role_diagnostic': 'SUGGESTION_CAUSE_ML',
                        'origine': 'MODELE_ML_ACTIF',
                    },
                )
            )
        return created
    except Exception:
        logger.exception(
            'Impossible de produire les suggestions ML pour l’anomalie #%s.',
            anomaly.pk,
        )
        return []
