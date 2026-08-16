from django import template

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
    """Stable semantic identity of the supervised discrepancy across campaigns.

    Do not use empreinte_anomalie here: fingerprints are implementation details and
    may evolve with engine versions. The register groups the same business problem
    by the fields a supervisor actually sees.
    """
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


@register.simple_tag
def group_business_anomalies(anomalies):
    """Return one visual dossier per business discrepancy.

    Model rows remain untouched: this only removes duplicate campaign occurrences
    from the register presentation. The representative receives transient metadata
    used by the template (occurrence_count, first_detected, last_detected).
    """
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
