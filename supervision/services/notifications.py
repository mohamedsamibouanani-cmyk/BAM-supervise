from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from supervision.models import (
    Anomalie, GroupeResponsable, HistoriqueAnomalie, Notification,
    NotificationDestinataire, RegleAffectation, Systeme, ValidationMotif,
)
from supervision.services.email_config import (
    EmailConfigurationError,
    validate_real_email_config,
)


class RoutingError(ValueError):
    pass


def _ensure_real_delivery_backend():
    """Prevent a simulated backend from being recorded as a real delivered notification."""
    try:
        validate_real_email_config()
    except EmailConfigurationError as exc:
        raise RoutingError(str(exc)) from exc


def _target_systems(validation):
    """Resolve every correction target while keeping legacy single-system validations compatible."""
    codes = []
    if validation.decision == ValidationMotif.Decision.ACCEPTE and validation.prediction_retenue_id:
        explanation = validation.prediction_retenue.explication or {}
        for code in explanation.get('systemes_a_corriger') or []:
            code = str(code or '').strip().upper()
            if code and code not in codes:
                codes.append(code)

    if not codes and validation.systeme_a_corriger_final_id:
        codes.append(validation.systeme_a_corriger_final.code_systeme)

    systems_by_code = {
        system.code_systeme: system
        for system in Systeme.objects.filter(code_systeme__in=codes, actif=True)
    }
    systems = [systems_by_code[code] for code in codes if code in systems_by_code]
    if not systems:
        raise RoutingError('Aucun système à corriger valide n’a été déterminé.')
    return systems


def route_group(validation, system):
    rule = RegleAffectation.objects.filter(
        systeme_a_corriger=system,
        motif=validation.motif_final,
        actif=True,
    ).select_related('groupe').order_by('priorite').first()
    if rule:
        return rule.groupe
    fallback = GroupeResponsable.objects.filter(
        systeme=system,
        actif=True,
    ).order_by('id').first()
    if fallback:
        return fallback
    raise RoutingError(f'Aucun groupe responsable configuré pour le système {system.code_systeme}.')


def build_email(validation, group, system):
    anomaly = validation.anomalie
    if anomaly.niveau == Anomalie.Niveau.ENVOI:
        element = f'Envoi {anomaly.code_envoi}'
    elif anomaly.niveau == Anomalie.Niveau.SERVICE:
        element = f'Service {anomaly.code_service} de l’envoi {anomaly.code_envoi}'
    else:
        element = f'Attribut {anomaly.attribut.code_attribut if anomaly.attribut_id else "-"} de l’envoi {anomaly.code_envoi}'
    diagnostic_field = validation.motif_final.champ_typique or '-'
    if validation.prediction_retenue_id:
        explanation = validation.prediction_retenue.explication or {}
        indices = explanation.get('indices') or []
        if indices and indices[0].get('champ'):
            diagnostic_field = indices[0]['champ']
    subject = f'[BAM Supervise][{system.code_systeme}] {anomaly.niveau} {anomaly.type_ecart} - {anomaly.code_envoi}'
    if anomaly.type_ecart == Anomalie.TypeEcart.DIFFERENT:
        gap_description = 'Valeurs observées : ' + ' ; '.join(
            f'{detail.systeme.code_systeme}={detail.valeur_brute or "—"}'
            for detail in anomaly.details.select_related('systeme').order_by('systeme__ordre_comparaison')
        )
    else:
        observed_system = anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '-'
        gap_description = (
            f'Système où l’élément manque : {observed_system}\n'
            f'Système où l’anomalie est observée : {observed_system}'
        )
    body = (
        f'Bonjour,\n\n'
        f'BAM Supervise a détecté une anomalie de synchronisation.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Élément non synchronisé : {element}\n'
        f'{gap_description}\n'
        f'Service : {anomaly.code_service or "-"}\n'
        f'Attribut : {anomaly.attribut.code_attribut if anomaly.attribut_id else "-"}\n'
        f'Motif validé : {validation.motif_final.libelle}\n'
        f'Champ source à vérifier : {diagnostic_field}\n'
        f'Système responsable de la correction : {system.code_systeme}\n'
        f'Commentaire : {validation.commentaire or "-"}\n\n'
        f'Merci de corriger la donnée dans votre système. '
        f'La résolution sera vérifiée lors du prochain import.\n\n'
        f'BAM Supervise'
    )
    return subject, body


def _send_one(validation, system):
    group = route_group(validation, system)

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
        raise RoutingError(f'Le groupe responsable de {system.code_systeme} ne possède aucune adresse e-mail enregistrée.')

    subject, body = build_email(validation, group, system)

    # Une notification distincte est persistée par système responsable.
    # Cela rend le routage multi-systèmes traçable sans codage spécifique SMI/SICOM/SIBO.
    with transaction.atomic():
        notification = Notification.objects.create(
            validation=validation,
            groupe=group,
            objet=subject,
            message=body,
        )
        rows = []
        snapshot_emails = set()
        for contact in contacts:
            if not contact.email or contact.email in snapshot_emails:
                continue
            snapshot_emails.add(contact.email)
            rows.append(NotificationDestinataire.objects.create(
                notification=notification,
                contact=contact,
                nom_snapshot=contact.nom_complet,
                email_snapshot=contact.email,
            ))
        if group.email_collectif and group.email_collectif not in snapshot_emails:
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


def send_validation_email(validation):
    """Notify every responsible system and keep one Notification row per routed system."""
    systems = _target_systems(validation)
    sent_notifications = []
    failures = []

    for system in systems:
        try:
            sent_notifications.append(_send_one(validation, system))
        except Exception as exc:
            failures.append((system.code_systeme, str(exc)))

    if sent_notifications:
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
            commentaire=(
                'Notifications envoyées aux systèmes responsables : '
                + ', '.join(notification.groupe.systeme.code_systeme for notification in sent_notifications)
                + '.'
            ),
        )

    if failures:
        details = ' ; '.join(f'{code}: {message}' for code, message in failures)
        if sent_notifications:
            raise RoutingError(f'Notification partielle : {details}')
        raise RoutingError(details)

    # Compatibilité avec l’API historique : pour un système unique, les appels existants
    # continuent de recevoir directement l’objet Notification.
    return sent_notifications[0]
