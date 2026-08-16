from django import template

from supervision.models import AttributDefinition
from supervision.services.security import decrypt_sensitive

register = template.Library()


def _source_attribute_value(anomaly, detail):
    """Return the real attribute value for one system while rendering a supervisor page.

    DetailComparaison intentionally stores masked values for sensitive attributes.
    The original sensitive value remains encrypted in ValeurAttributSnapshot and is
    decrypted only in memory for the authenticated supervisor detail page.
    """
    if not anomaly.attribut_id or not anomaly.attribut.sensible:
        return detail.valeur_brute or '—'

    link = (
        anomaly.campagne.imports.select_related('fichier_import')
        .filter(systeme=detail.systeme)
        .first()
    )
    if not link:
        return detail.valeur_brute or '—'

    shipment = link.fichier_import.envois.filter(code_envoi=anomaly.code_envoi).first()
    if not shipment:
        return detail.valeur_brute or '—'

    if anomaly.attribut.portee == AttributDefinition.Portee.ENVOI:
        value = shipment.valeurs_attribut.filter(attribut=anomaly.attribut).first()
    else:
        service = (
            shipment.services.filter(code_service=anomaly.code_service)
            .order_by('numero_occurrence')
            .first()
        )
        value = service.valeurs_attribut.filter(attribut=anomaly.attribut).first() if service else None

    if value is None or value.est_vide or not value.valeur_brute:
        return '—'

    try:
        return decrypt_sensitive(value.valeur_brute) or '—'
    except ValueError:
        return 'Valeur indisponible'


@register.simple_tag
def display_anomaly_value(anomaly, detail):
    """Display the real comparison value for an attribute anomaly.

    Sensitive attributes such as telephone numbers are decrypted only in memory for
    the authenticated supervisor page. This applies to ABSENT as well as DIFFERENT
    anomalies so the supervisor can see the values actually present in each system.
    """
    if anomaly.niveau == 'ATTRIBUT':
        return _source_attribute_value(anomaly, detail)
    return detail.valeur_brute or '—'
