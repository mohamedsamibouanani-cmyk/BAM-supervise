from __future__ import annotations

from collections import Counter

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, mean_squared_error
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
    return FORECAST_LABELS[int(np.argmax(vector))][0]


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


def _features(vectors, index):
    return np.concatenate([vectors[index], [float(index + 1)]])


def _new_regression_model():
    return RandomForestRegressor(
        n_estimators=200,
        max_depth=4,
        min_samples_leaf=1,
        random_state=42,
    )


def _new_classification_model():
    return Pipeline([
        ('scaler', StandardScaler()),
        ('classifier', LogisticRegression(
            max_iter=1000,
            class_weight='balanced',
            random_state=42,
        )),
    ])


def _evaluation_maturity(rf_count):
    if rf_count <= 0:
        return 'NON_DISPONIBLE', 'Non disponible'
    if rf_count <= 2:
        return 'EXPLORATOIRE', 'Exploratoire'
    if rf_count <= 5:
        return 'INTERMEDIAIRE', 'Intermédiaire'
    return 'RENFORCEE', 'Renforcée'


def _rolling_evaluation(vectors):
    """Backtesting walk-forward : chaque campagne est prédite sans voir son futur."""
    label_by_code = dict(FORECAST_LABELS)
    rf_actual_totals = []
    rf_predicted_totals = []
    rf_actual_vectors = []
    rf_predicted_vectors = []
    logistic_actual = []
    logistic_predicted = []
    last_rf_backtest = None
    last_classification_backtest = None

    # C1->C2 constitue le premier apprentissage ; C3 est donc le premier vrai test.
    for target_index in range(2, len(vectors)):
        x_train = []
        y_reg_train = []
        y_cls_train = []
        for source_index in range(0, target_index - 1):
            x_train.append(_features(vectors, source_index))
            y_reg_train.append(vectors[source_index + 1])
            y_cls_train.append(_dominant_code(vectors[source_index + 1]))

        if not x_train:
            continue

        test_features = _features(vectors, target_index - 1)
        actual_vector = vectors[target_index]

        rf = _new_regression_model()
        rf.fit(np.asarray(x_train), np.asarray(y_reg_train))
        rf_prediction = np.maximum(0, np.rint(rf.predict([test_features])[0])).astype(int)

        actual_total = int(np.sum(actual_vector))
        predicted_total = int(np.sum(rf_prediction))
        absolute_error = abs(actual_total - predicted_total)
        rf_actual_vectors.append(actual_vector)
        rf_predicted_vectors.append(rf_prediction)
        rf_actual_totals.append(float(actual_total))
        rf_predicted_totals.append(float(predicted_total))
        last_rf_backtest = {
            'campaign_number': target_index + 1,
            'predicted_total': predicted_total,
            'actual_total': actual_total,
            'absolute_error': absolute_error,
        }

        valid_rows = [
            (features, label)
            for features, label in zip(x_train, y_cls_train)
            if label is not None
        ]
        labels = [row[1] for row in valid_rows]
        actual_class = _dominant_code(actual_vector)
        if actual_class is not None and len(set(labels)) >= 2:
            classifier = _new_classification_model()
            classifier.fit(
                np.asarray([row[0] for row in valid_rows]),
                np.asarray(labels),
            )
            predicted_class = str(classifier.predict([test_features])[0])
            logistic_predicted.append(predicted_class)
            logistic_actual.append(actual_class)
            last_classification_backtest = {
                'campaign_number': target_index + 1,
                'predicted_type': label_by_code.get(predicted_class, predicted_class),
                'actual_type': label_by_code.get(actual_class, actual_class),
                'correct': predicted_class == actual_class,
            }

    rf_count = len(rf_actual_totals)
    maturity_code, maturity_label = _evaluation_maturity(rf_count)
    result = {
        'evaluation_method': 'Validation chronologique walk-forward',
        'evaluation_maturity': maturity_code,
        'evaluation_maturity_label': maturity_label,
        'rf_evaluation_count': rf_count,
        'rf_mae_total': None,
        'rf_rmse_total': None,
        'rf_mae_by_type': None,
        'last_rf_backtest': last_rf_backtest,
        'classification_evaluation_count': len(logistic_actual),
        'classification_accuracy': None,
        'classification_f1_macro': None,
        'last_classification_backtest': last_classification_backtest,
    }

    if rf_actual_totals:
        result['rf_mae_total'] = round(
            float(mean_absolute_error(rf_actual_totals, rf_predicted_totals)), 2
        )
        result['rf_rmse_total'] = round(
            float(np.sqrt(mean_squared_error(rf_actual_totals, rf_predicted_totals))), 2
        )
        actual_matrix = np.asarray(rf_actual_vectors)
        predicted_matrix = np.asarray(rf_predicted_vectors)
        per_type = np.mean(np.abs(actual_matrix - predicted_matrix), axis=0)
        result['rf_mae_by_type'] = [
            {'code': code, 'label': label, 'mae': round(float(value), 2)}
            for (code, label), value in zip(FORECAST_LABELS, per_type)
        ]

    if logistic_actual:
        result['classification_accuracy'] = round(
            float(accuracy_score(logistic_actual, logistic_predicted)) * 100, 1
        )
        result['classification_f1_macro'] = round(
            float(f1_score(
                logistic_actual,
                logistic_predicted,
                average='macro',
                zero_division=0,
            )) * 100,
            1,
        )

    return result


def campaign_forecast():
    """Prévoit la prochaine campagne sans intervenir dans la détection métier.

    Random Forest estime les volumes par type. La régression logistique estime le
    type dominant. Le moteur déterministe SMI/SICOM/SIBO reste l'unique source de
    vérité après import.
    """
    campaigns = _history()
    vectors = [_campaign_vector(campaign) for campaign in campaigns]
    highlights = _historical_highlights(campaigns)
    evaluation = _rolling_evaluation(vectors)
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
        **evaluation,
    }

    if len(campaigns) < MIN_CAMPAIGNS_FOR_FORECAST:
        return base

    x = []
    y_regression = []
    y_classification = []
    for index in range(len(vectors) - 1):
        features = _features(vectors, index)
        x.append(features)
        y_regression.append(vectors[index + 1])
        y_classification.append(_dominant_code(vectors[index + 1]))

    x_array = np.asarray(x)
    y_regression_array = np.asarray(y_regression)
    regression_model = _new_regression_model()
    regression_model.fit(x_array, y_regression_array)

    next_features = _features(vectors, len(vectors) - 1)
    predicted = np.maximum(
        0,
        np.rint(regression_model.predict([next_features])[0]),
    ).astype(int)

    # La fourchette vient d'abord de l'erreur réellement observée en backtesting.
    backtest_mae = evaluation.get('rf_mae_total')
    margin = max(1, int(round(backtest_mae))) if backtest_mae is not None else 1
    predicted_total = int(predicted.sum())

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
        classification_model = _new_classification_model()
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
    }
