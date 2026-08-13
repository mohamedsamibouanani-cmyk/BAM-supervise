from supervision.models import (
    Anomalie, HistoriqueAnomalie, Notification, NotificationDestinataire,
)

from .notifications import RoutingError, _send_one, _target_systems


def retry_failed_notification(notification):
    """Retry only the failed recipients of the current responsible system.

    The failed notification remains immutable for audit purposes. Current routing
    is recomputed first. For notifications created with per-recipient delivery,
    only recipient rows marked ECHEC are retried; collaborators who already
    received the message are never contacted again by this retry.
    """
    if notification.statut != Notification.Statut.ECHEC:
        raise RoutingError('Seules les notifications en échec peuvent être relancées.')

    system = notification.groupe.systeme
    current_targets = {
        item.code_systeme: item
        for item in _target_systems(notification.validation)
    }
    if system.code_systeme not in current_targets:
        raise RoutingError(
            f'La tentative {system.code_systeme} est obsolète : ce système n’est plus '
            'identifié comme responsable par le diagnostic actuel. Révisez le dossier '
            'avant toute nouvelle notification.'
        )

    failed_emails = list(
        notification.destinataires.filter(
            statut_livraison=NotificationDestinataire.Statut.ECHEC
        ).values_list('email_snapshot', flat=True)
    )
    # Compatibility with historical failed notifications that predate recipient
    # delivery statuses: if no failed row exists, recompute the whole current group.
    only_emails = failed_emails or None

    retried = _send_one(
        notification.validation,
        current_targets[system.code_systeme],
        only_emails=only_emails,
    )

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
            commentaire=(
                f'Notification relancée avec succès vers {system.code_systeme} '
                f'pour {len(retried.destinataires.all())} destinataire(s) en échec.'
            ),
        )

    return retried
