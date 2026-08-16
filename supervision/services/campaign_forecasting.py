from __future__ import annotations

from collections import Counter

import numpy as np
from sklearn.ensemble import RandomForestRegressor

from supervision.models import Anomalie, CampagneSupervision


MIN_CAMPAIGNS_FOR_FORECAST = 6
FORECAST_LABELS = (
    ('ENVOI_ABSENT', 'Envoi absent'),
    ('SERVICE_ABSENT', 'Service absent'),
    ('ATTRIBUT_ABSENT', "Valeur d’attribut absente"),
    ('ATTRIBUT_DIFFERENT', "Valeur d’attribut différente"),
)


def _bucket(anomaly):
    if anomaly.niveau == Anomalie.Niveau.ENVOI:
        return 'ENVOI_ABSENT'
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return 'SERVICE_ABSENT'
    if anomaly.type_ecart == Anomalie.TypeEcart.DIFFERENT:
        return 'ATTRIBUT_DIFFERENT'
    return 'ATTRIBUT_ABSENT'


def _campaign_vector(campaign):
    counts = Counter(_bucket(a) for a in campaign.anomalies.all())
    return np.array([float(counts.get(code, 0)) for code, _label in FORECAST_LABELS], dtype=float)


def _history():
    return list(
        CampagneSupervision.objects.filter(statut=CampagneSupervision.Statut.TERMINEE)
        .prefetch_related('anomalies__attribut', 'anomalies__systeme_ecart')
        .order_by('demarree_le', 'pk')
    )


def _historical_highlights(campaigns):
    attribute_counts = Counter()
    system_counts = Counter()
    bucket_counts = Counter()
    for campaign in campaigns:
        for anomaly in campaign.anomalies.all():
            bucket_counts[_bucket(anomaly)] += 1
            if anomaly.attribut_id:
                attribute_counts[anomaly.attribut.code_attribut] += 1
            if anomaly.systeme_ecart_id:
                system_counts[anomaly.systeme_ecart.code_systeme] += 1

    label_by_code = dict(FORECAST_LABELS)
    dominant_code = bucket_counts.most_common(1)[0][0] if bucket_counts else None
    return {
        'historical_dominant_type': label_by_code.get(dominant_code) if dominant_code else None,
        'historical_dominant_attribute': attribute_counts.most_common(1)[0][0] if attribute_counts else None,
        'historical_dominant_system': system_counts.most_common(1)[0][0] if system_counts else None,
    }


def campaign_forecast():
    """Prévoit la structure de la prochaine campagne à partir des campagnes passées.

    Le moteur déterministe reste l'unique source de vérité après import. Le ML
    n'intervient qu'avant la campagne suivante et ne crée/modifie aucune anomalie.
    """
    campaigns = _history()
    vectors = [_campaign_vector(campaign) for campaign in campaigns]
    highlights = _historical_highlights(campaigns)
    base = {
        'ready': False,
        'campaign_count': len(campaigns),
        'minimum_campaigns': MIN_CAMPAIGNS_FOR_FORECAST,
        'remaining_campaigns': max(0, MIN_CAMPAIGNS_FOR_FORECAST - len(campaigns)),
        'method': 'Random Forest sur historique de campagnes',
        **highlights,
    }

    if len(campaigns) < MIN_CAMPAIGNS_FOR_FORECAST:
        return base

    # Une campagne constitue un point temporel. On apprend le passage t -> t+1.
    # Les variables incluent la répartition de t et son indice temporel afin de
    # capter à la fois récurrence et tendance sans mélanger ce modèle au moteur métier.
    x = []
    y = []
    for index in range(len(vectors) - 1):
        x.append(np.concatenate([vectors[index], [float(index + 1)]]))
        y.append(vectors[index + 1])

    model = RandomForestRegressor(
        n_estimators=200,
        max_depth=4,
        min_samples_leaf=1,
        random_state=42,
    )
    model.fit(np.asarray(x), np.asarray(y))

    next_index = float(len(vectors))
    next_features = np.concatenate([vectors[-1], [next_index]])
    raw_prediction = model.predict([next_features])[0]
    predicted = np.maximum(0, np.rint(raw_prediction)).astype(int)

    # Erreur d'apprentissage utilisée uniquement comme bande indicative, jamais
    # comme probabilité. Avec un petit historique, elle doit être lue prudemment.
    fitted = model.predict(np.asarray(x))
    mae_total = float(np.mean(np.abs(np.sum(fitted, axis=1) - np.sum(np.asarray(y), axis=1))))
    predicted_total = int(predicted.sum())
    margin = max(1, int(round(mae_total)))

    distribution = []
    for (code, label), value in zip(FORECAST_LABELS, predicted):
        value = int(value)
        pct = round((value / predicted_total * 100), 1) if predicted_total else 0
        distribution.append({'code': code, 'label': label, 'count': value, 'percent': pct})

    dominant = max(distribution, key=lambda row: row['count']) if distribution else None
    last_total = int(vectors[-1].sum()) if vectors else 0
    if predicted_total < last_total:
        trend = 'BAISSE'
    elif predicted_total > last_total:
        trend = 'HAUSSE'
    else:
        trend = 'STABLE'

    return {
        **base,
        'ready': True,
        'predicted_total': predicted_total,
        'interval_low': max(0, predicted_total - margin),
        'interval_high': predicted_total + margin,
        'distribution': distribution,
        'dominant_type': dominant['label'] if dominant else None,
        'dominant_type_count': dominant['count'] if dominant else 0,
        'last_campaign_total': last_total,
        'trend': trend,
        'training_pairs': len(x),
        'mae_total': round(mae_total, 2),
    }
