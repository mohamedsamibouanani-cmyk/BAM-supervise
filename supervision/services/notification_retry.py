from supervision.models import Anomalie, HistoriqueAnomalie, Notification

from .notifications import RoutingError, _send_one, _target_systems


def retry_failed_notification(notification):
    """Retry only the system attached to one failed notification.

    The failed record remains immutable for audit purposes. Before retrying, the
    current routing is recomputed. If the old notification points to a system that
    is no longer responsible for the dossier, the retry is refused instead of
    sending a stale or duplicate message.
    """
    if notification.statut != Notification.Statut.ECHEC:
        raise RoutingError('Seules les notifications en échec peuvent être relancées.')

    system = notification.groupe.systeme
    current_targets = {item.code_systeme: item for item in _target_systems(notification.validation)}
    if system.code_systeme not in current_targets:
        raise RoutingError(
            f'La tentative {system.code_systeme} est obsolète : ce système n’est plus '
            'identifié comme responsable par le diagnostic actuel. Révisez le dossier '
            'avant toute nouvelle notification.'
        )

    retried = _send_one(notification.validation, current_targets[system.code_systeme])

    anomaly = notification.validation.anomalie
    if anomaly.statut != Anomalie.Statut.NOTIFIEE:
        old_status = anomaly.statut
        anomaly.statut = Anomalie.Statut.NOTIFIEE
        anomaly.save(update_fields=['statut'])
        HistoriqueAnomalie.objects.create(
            anomalie=anomaly,
            ancien_statut=old_status,
            nouveau_statut=Anomalie.Statut.NOTIFIEE,
            source_evenement='MAIL',
            superviseur=notification.validation.superviseur,
            commentaire=f'Notification relancée avec succès vers {system.code_systeme}.',
        )

    return retried
