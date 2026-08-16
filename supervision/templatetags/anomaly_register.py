from django import template

from supervision.models import Anomalie

register = template.Library()


_STATUS_PRIORITY = {
    'PERSISTANTE': 60,
    'NOTIFIEE': 50,
    'VALIDEE': 40,
    'ANALYSEE': 30,
    'DETECTEE': 20,
    'RESOLUE': 10,
}


def _business_key(anomaly):
    """Stable semantic identity of the supervised discrepancy across campaigns."""
    return (
        anomaly.code_envoi or '',
        anomaly.niveau or '',
        anomaly.type_ecart or '',
        anomaly.code_service or '',
        anomaly.attribut.code_attribut if anomaly.attribut_id else '',
        anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
    )


def _representative(group):
    """Keep the status that best represents the current business follow-up."""
    return max(
        group,
        key=lambda anomaly: (
            _STATUS_PRIORITY.get(anomaly.statut, 0),
            anomaly.detectee_le,
            anomaly.pk,
        ),
    )


def _group(anomalies):
    groups = {}
    order = []
    for anomaly in list(anomalies):
        key = _business_key(anomaly)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(anomaly)

    result = []
    for key in order:
        group = groups[key]
        representative = _representative(group)
        representative.occurrence_count = len(group)
        representative.first_detected = min(item.detectee_le for item in group)
        representative.last_detected = max(item.detectee_le for item in group)
        result.append(representative)
    return result


@register.simple_tag
def group_business_anomalies(anomalies):
    """Return one visual dossier per business discrepancy."""
    return _group(anomalies)


@register.simple_tag
def business_anomaly_summary():
    """Dashboard/register counters based on business dossiers, not campaign rows."""
    anomalies = Anomalie.objects.select_related('attribut', 'systeme_ecart').all()
    dossiers = _group(anomalies)
    action = {'DETECTEE', 'ANALYSEE'}
    follow_up = {'VALIDEE', 'NOTIFIEE', 'PERSISTANTE'}
    resolved = {'RESOLUE'}
    a_traiter = sum(1 for item in dossiers if item.statut in action)
    en_suivi = sum(1 for item in dossiers if item.statut in follow_up)
    resolues = sum(1 for item in dossiers if item.statut in resolved)
    return {
        'total': len(dossiers),
        'a_traiter': a_traiter,
        'a_valider': a_traiter,
        'en_suivi': en_suivi,
        'resolues': resolues,
        'taux_resolution': round((resolues / len(dossiers) * 100), 1) if dossiers else 0,
    }
