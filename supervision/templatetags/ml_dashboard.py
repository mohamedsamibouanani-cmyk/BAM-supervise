from django import template

from supervision.models import ExempleApprentissage, ModeleML
from supervision.services.ml_training import MIN_TRAINING_EXAMPLES

register = template.Library()


@register.simple_tag
def ml_dashboard_state():
    """Return a compact, presentation-ready state of BAM Supervise ML learning."""
    active_model = ModeleML.objects.filter(actif=True).order_by('-entraine_le').first()
    eligible_examples = ExempleApprentissage.objects.filter(eligible=True).count()
    pending_examples = ExempleApprentissage.objects.filter(eligible=True, version_dataset='').count()

    metrics = active_model.metriques if active_model and active_model.metriques else {}
    accuracy = metrics.get('accuracy_apprentissage')
    accuracy_percent = round(float(accuracy) * 100, 1) if accuracy is not None else None
    class_count = metrics.get('nb_classes')

    if active_model:
        status = 'ACTIF'
        status_label = 'Modèle actif'
        status_hint = 'Les nouvelles anomalies peuvent recevoir des suggestions issues du Machine Learning.'
    elif eligible_examples < MIN_TRAINING_EXAMPLES:
        status = 'APPRENTISSAGE'
        status_label = 'Apprentissage en cours'
        status_hint = f'{eligible_examples}/{MIN_TRAINING_EXAMPLES} exemples validés disponibles avant le premier entraînement.'
    else:
        status = 'PRET'
        status_label = 'Prêt à apprendre'
        status_hint = 'Les données sont suffisantes ; le modèle sera créé après validation complète d’une campagne.'

    return {
        'active_model': active_model,
        'status': status,
        'status_label': status_label,
        'status_hint': status_hint,
        'eligible_examples': eligible_examples,
        'pending_examples': pending_examples,
        'minimum_examples': MIN_TRAINING_EXAMPLES,
        'accuracy_percent': accuracy_percent,
        'class_count': class_count,
    }
