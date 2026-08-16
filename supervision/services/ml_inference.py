from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import joblib
from django.db.models import Max

from supervision.models import Anomalie, ModeleML, Motif, PredictionMotif


logger = logging.getLogger(__name__)


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


def _active_or_new_model():
    """Retourne le modèle actif, ou tente un premier entraînement à la demande.

    Ce rattrapage est important lorsque l'application possède déjà assez de
    validations historiques au moment d'un déploiement : aucun nouvel exemple
    n'est alors créé pour déclencher le signal post_save. L'ouverture d'un dossier
    analysé suffit donc à rendre l'aide ML disponible dès que le seuil est atteint.
    """
    model = ModeleML.objects.filter(actif=True).order_by('-entraine_le').first()
    if model is not None:
        return model

    # Import local pour éviter tout couplage au chargement des modules de signaux.
    from .ml_training import safe_maybe_retrain_model

    model = safe_maybe_retrain_model()
    if model is not None:
        return model
    return ModeleML.objects.filter(actif=True).order_by('-entraine_le').first()


def ensure_ml_predictions(anomaly, max_suggestions=2):
    """Ajoute les suggestions du modèle actif même si des règles ont déjà conclu.

    Les règles métier et les cas appris restent des sources séparées. Seules les
    lignes ``source_prediction=ML`` portent une probabilité affichable. Le modèle
    reste une aide à la décision : le superviseur conserve la validation finale.
    """
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return []

    model = _active_or_new_model()
    if model is None:
        return []

    existing = list(
        anomaly.predictions.filter(
            source_prediction=PredictionMotif.Source.ML,
            modele=model,
        ).order_by('rang')
    )
    if existing:
        return existing

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
            if motif is None:
                continue

            last_rank += 1
            target_codes = (
                [anomaly.systeme_ecart.code_systeme]
                if anomaly.systeme_ecart_id
                else []
            )
            created.append(
                PredictionMotif.objects.create(
                    anomalie=anomaly,
                    source_prediction=PredictionMotif.Source.ML,
                    modele=model,
                    motif=motif,
                    # La suggestion ML porte sur le motif. Le système observé reste
                    # un indice de contexte et la cible finale reste une décision humaine.
                    systeme_a_corriger_predit=anomaly.systeme_ecart,
                    rang=last_rank,
                    score_confiance=Decimal(
                        str(round(float(probability), 4))
                    ),
                    explication={
                        'message': motif.libelle,
                        'features': features,
                        'systemes_a_corriger': target_codes,
                        'role_diagnostic': 'SUGGESTION',
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
