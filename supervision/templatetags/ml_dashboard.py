from django import template

from supervision.models import ModeleML, PredictionMotif
from supervision.services.ml_training import safe_maybe_retrain_model, training_status


register = template.Library()


@register.inclusion_tag('supervision/includes/ml_dashboard_card.html')
def ml_dashboard_card():
    status = training_status()
    model = status['active_model']

    if model is None and status['ready']:
        model = safe_maybe_retrain_model()
        status = training_status()
        model = status['active_model'] or model

    metrics = model.metriques or {} if model else {}
    accuracy = metrics.get('accuracy_validation')
    return {
        'ml_model': model,
        'ml_ready': status['ready'],
        'ml_examples': status['eligible_examples'],
        'ml_trainable_examples': status['trainable_examples'],
        'ml_excluded_examples': status['excluded_examples'],
        'ml_minimum_examples': status['minimum_examples'],
        'ml_motif_classes': status['motif_classes'],
        'ml_trainable_classes': status['trainable_classes'],
        'ml_accuracy_percent': round(float(accuracy) * 100, 1) if accuracy is not None else None,
        'ml_validation_size': metrics.get('nb_validation'),
        'ml_prediction_count': PredictionMotif.objects.filter(
            source_prediction=PredictionMotif.Source.ML
        ).count(),
        'ml_model_count': ModeleML.objects.count(),
    }
