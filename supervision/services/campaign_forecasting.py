from __future__ import annotations

from collections import Counter

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from supervision.models import Anomalie, CampagneSupervision


MIN_CAMPAIGNS_FOR_FORECAST = 3
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


def _dominant_code(vector):
    if vector is None or len(vector) == 0 or float(np.sum(vector)) <= 0:
        return None
    index = int(np.argmax(vector))
    return FORECAST_LABELS[index][0]


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
    """Prévoit la prochaine campagne avec deux modèles complémentaires.

    Random Forest = régression du nombre d'anomalies par type.
    Régression logistique = classification du type d'anomalie dominant.

    Le moteur déterministe reste l'unique source de vérité après import. Les
    modèles n'interviennent qu'avant la campagne suivante et ne créent/modifient
    aucune anomalie.

    Pour la démonstration BAM Supervise, la prévision démarre à partir de trois
    campagnes terminées. Avec seulement trois campagnes, le résultat est donc une
    estimation exploratoire : il devient progressivement plus robuste lorsque
    l'historique s'allonge.
    """
    campaigns = _history()
    vectors = [_campaign_vector(campaign) for campaign in campaigns]
    highlights = _historical_highlights(campaigns)
    base = {
        'ready': False,
        'campaign_count': len(campaigns),
        'minimum_campaigns': MIN_CAMPAIGNS_FOR_FORECAST,
        'remaining_campaigns': max(0, MIN_CAMPAIGNS_FOR_FORECAST - len(campaigns)),
        'method': 'Random Forest + Régression logistique',
        'regression_method': 'Random Forest Regressor',
        'classification_method': 'Régression logistique multinomiale',
        'classification_ready': False,
        'classification_probability': None,
        'classification_dominant_type': None,
        'forecast_maturity': 'EXPLORATOIRE' if len(campaigns) < 6 else 'RENFORCEE',
        **highlights,
    }

    if len(campaigns) < MIN_CAMPAIGNS_FOR_FORECAST:
        return base

    x = []
    y_regression = []
    y_classification = []
    for index in range(len(vectors) - 1):
        features = np.concatenate([vectors[index], [float(index + 1)]])
        x.append(features)
        y_regression.append(vectors[index + 1])
        y_classification.append(_dominant_code(vectors[index + 1]))

    x_array = np.asarray(x)
    y_regression_array = np.asarray(y_regression)

    regression_model = RandomForestRegressor(
        n_estimators=200,
        max_depth=4,
        min_samples_leaf=1,
        random_state=42,
    )
    regression_model.fit(x_array, y_regression_array)

    next_index = float(len(vectors))
    next_features = np.concatenate([vectors[-1], [next_index]])
    raw_prediction = regression_model.predict([next_features])[0]
    predicted = np.maximum(0, np.rint(raw_prediction)).astype(int)

    fitted = regression_model.predict(x_array)
    mae_total = float(
        np.mean(
            np.abs(
                np.sum(fitted, axis=1)
                - np.sum(y_regression_array, axis=1)
            )
        )
    )
    predicted_total = int(predicted.sum())
    margin = max(1, int(round(mae_total)))

    distribution = []
    for (code, label), value in zip(FORECAST_LABELS, predicted):
        value = int(value)
        pct = round((value / predicted_total * 100), 1) if predicted_total else 0
        distribution.append({'code': code, 'label': label, 'count': value, 'percent': pct})

    rf_dominant = max(distribution, key=lambda row: row['count']) if distribution else None

    label_by_code = dict(FORECAST_LABELS)
    valid_classification_rows = [
        (features, label)
        for features, label in zip(x, y_classification)
        if label is not None
    ]
    classification_ready = False
    classification_probability = None
    classification_code = None
    class_distribution = []

    classification_labels = [row[1] for row in valid_classification_rows]
    if len(set(classification_labels)) >= 2:
        x_classification = np.asarray([row[0] for row in valid_classification_rows])
        y_classification_array = np.asarray(classification_labels)
        classification_model = Pipeline([
            ('scaler', StandardScaler()),
            ('classifier', LogisticRegression(
                max_iter=1000,
                class_weight='balanced',
                random_state=42,
            )),
        ])
        classification_model.fit(x_classification, y_classification_array)
        probabilities = classification_model.predict_proba([next_features])[0]
        classes = classification_model.named_steps['classifier'].classes_
        best_index = int(np.argmax(probabilities))
        classification_code = str(classes[best_index])
        classification_probability = round(float(probabilities[best_index]) * 100, 1)
        class_distribution = [
            {
                'code': str(code),
                'label': label_by_code.get(str(code), str(code)),
                'probability': round(float(probability) * 100, 1),
            }
            for code, probability in sorted(
                zip(classes, probabilities),
                key=lambda item: float(item[1]),
                reverse=True,
            )
        ]
        classification_ready = True

    dominant_type = (
        label_by_code.get(classification_code)
        if classification_ready
        else (rf_dominant['label'] if rf_dominant else None)
    )

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
        'dominant_type': dominant_type,
        'dominant_type_count': rf_dominant['count'] if rf_dominant else 0,
        'rf_dominant_type': rf_dominant['label'] if rf_dominant else None,
        'classification_ready': classification_ready,
        'classification_probability': classification_probability,
        'classification_dominant_type': (
            label_by_code.get(classification_code) if classification_ready else None
        ),
        'classification_distribution': class_distribution,
        'last_campaign_total': last_total,
        'trend': trend,
        'training_pairs': len(x),
        'classification_training_pairs': len(valid_classification_rows),
        'mae_total': round(mae_total, 2),
    }
