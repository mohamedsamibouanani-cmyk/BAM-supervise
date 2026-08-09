from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from supervision.models import (
    Anomalie, GroupeResponsable, HistoriqueAnomalie, Notification,
    NotificationDestinataire, RegleAffectation,
)


class RoutingError(ValueError):
    pass


SIMULATED_EMAIL_BACKENDS = {
    'django.core.mail.backends.console.EmailBackend',
    'django.core.mail.backends.locmem.EmailBackend',
    'django.core.mail.backends.dummy.EmailBackend',
    'django.core.mail.backends.filebased.EmailBackend',
}


def _ensure_real_delivery_backend():
    """Prevent a simulated backend from being recorded as a real delivered notification."""
    if settings.EMAIL_BACKEND in SIMULATED_EMAIL_BACKENDS and not getattr(
        settings, 'EMAIL_ALLOW_SIMULATED_DELIVERY', False
    ):
        raise RoutingError(
            'La messagerie est encore en mode test. Configurez un serveur SMTP réel '
            'pour envoyer des notifications aux collaborateurs.'
        )


def route_group(validation):
    rule = RegleAffectation.objects.filter(
        systeme_a_corriger=validation.systeme_a_corriger_final,
        motif=validation.motif_final,
        actif=True,
    ).select_related('groupe').order_by('priorite').first()
    if rule:
        return rule.groupe
    fallback = GroupeResponsable.objects.filter(
        systeme=validation.systeme_a_corriger_final,
        actif=True,
    ).order_by('id').first()
    if fallback:
        return fallback
    raise RoutingError('Aucun groupe responsable configuré pour le système à corriger.')


def build_email(validation, group):
    anomaly = validation.anomalie
    subject = f'[BAM Supervise] {anomaly.niveau} {anomaly.type_ecart} - {anomaly.code_envoi}'
    body = (
        f'Bonjour,\n\n'
        f'BAM Supervise a détecté une anomalie de synchronisation.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Niveau : {anomaly.niveau}\n'
        f'Type d\'écart : {anomaly.type_ecart}\n'
        f'Service : {anomaly.code_service or "-"}\n'
        f'Attribut : {anomaly.attribut.code_attribut if anomaly.attribut_id else "-"}\n'
        f'Motif validé : {validation.motif_final.libelle}\n'
        f'Système à corriger : {validation.systeme_a_corriger_final.code_systeme}\n'
        f'Commentaire : {validation.commentaire or "-"}\n\n'
        f'Merci de corriger la donnée dans le système concerné. '
        f'La résolution sera vérifiée lors du prochain import.\n\n'
        f'BAM Supervise'
    )
    return subject, body


def send_validation_email(validation):
    group = route_group(validation)

    # Tous les collaborateurs enregistrés pour le groupe sont destinataires.
    # BAM Supervise ne maintient pas de statut métier actif/inactif des collaborateurs.
    contacts = list(group.contacts.all().order_by('id'))
    emails = []
    for contact in contacts:
        if contact.email and contact.email not in emails:
            emails.append(contact.email)
    if group.email_collectif and group.email_collectif not in emails:
        emails.append(group.email_collectif)
    if not emails:
        raise RoutingError('Le groupe responsable ne possède aucune adresse e-mail enregistrée.')

    subject, body = build_email(validation, group)

    # Persist notification intent and recipient snapshots atomically. The SMTP call is
    # deliberately outside this transaction so a delivery failure remains auditable.
    with transaction.atomic():
        notification = Notification.objects.create(
            validation=validation,
            groupe=group,
            objet=subject,
            message=body,
        )
        rows = []
        for c in contacts:
            if not c.email:
                continue
            rows.append(NotificationDestinataire.objects.create(
                notification=notification,
                contact=c,
                nom_snapshot=c.nom_complet,
                email_snapshot=c.email,
            ))
        if group.email_collectif:
            rows.append(NotificationDestinataire.objects.create(
                notification=notification,
                nom_snapshot=group.nom_groupe,
                email_snapshot=group.email_collectif,
            ))

    notification.nb_tentatives += 1
    try:
        _ensure_real_delivery_backend()
        reply_to = [settings.EMAIL_REPLY_TO] if settings.EMAIL_REPLY_TO else None
        sent = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=emails,
            reply_to=reply_to,
        ).send(fail_silently=False)
        if sent != 1:
            raise RuntimeError("Le backend e-mail n'a pas confirmé l'envoi du message.")

        now = timezone.now()
        with transaction.atomic():
            notification.statut = Notification.Statut.ENVOYEE
            notification.envoyee_le = now
            notification.erreur = ''
            notification.save(update_fields=['nb_tentatives', 'statut', 'envoyee_le', 'erreur'])
            for row in rows:
                row.statut_livraison = NotificationDestinataire.Statut.ENVOYE
                row.envoyee_le = now
                row.save(update_fields=['statut_livraison', 'envoyee_le'])
            anomaly = validation.anomalie
            old = anomaly.statut
            anomaly.statut = Anomalie.Statut.NOTIFIEE
            anomaly.save(update_fields=['statut'])
            HistoriqueAnomalie.objects.create(
                anomalie=anomaly,
                ancien_statut=old,
                nouveau_statut=Anomalie.Statut.NOTIFIEE,
                source_evenement='MAIL',
                superviseur=validation.superviseur,
                commentaire=f'E-mail envoyé au groupe {group.nom_groupe}.',
            )
        return notification
    except Exception as exc:
        # Failure status must survive the raised exception so the UI can display it
        # and the supervisor can retry later.
        with transaction.atomic():
            notification.statut = Notification.Statut.ECHEC
            notification.erreur = str(exc)[:2000]
            notification.save(update_fields=['nb_tentatives', 'statut', 'erreur'])
            for row in rows:
                row.statut_livraison = NotificationDestinataire.Statut.ECHEC
                row.erreur = str(exc)[:500]
                row.save(update_fields=['statut_livraison', 'erreur'])
        raise
