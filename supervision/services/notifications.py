from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from supervision.models import (
    Anomalie, GroupeResponsable, HistoriqueAnomalie, Notification,
    NotificationDestinataire, RegleAffectation, Systeme, ValidationMotif,
)
from supervision.services.analysis import prediction_target_codes
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


def _delivery_error_message(exc):
    """Turn provider errors into an actionable supervisor-facing message."""
    raw = str(exc).strip()
    lowered = raw.lower()
    if '525' in lowered and 'unauthorized ip address' in lowered:
        return (
            'Brevo a refusé l’envoi : l’adresse IP sortante de BAM Supervise n’est pas '
            'autorisée pour la clé SMTP (erreur 525). Autorisez cette IP dans Brevo '
            'Paramètres > Sécurité > IP autorisées, puis relancez la notification.'
        )
    return raw or exc.__class__.__name__


def _active_validations(validation):
    """Return the current cause set for the dossier.

    A legacy dossier has one final ValidationMotif. A multi-cause dossier has one
    final ValidationMotif per accepted cause. Old revisions are excluded.
    """
    if not validation.est_finale:
        return [validation]
    rows = list(
        validation.anomalie.validations.filter(est_finale=True)
        .select_related(
            'motif_final', 'systeme_a_corriger_final',
            'prediction_retenue__motif', 'prediction_retenue__systeme_a_corriger_predit',
            'prediction_retenue__regle__flux__systeme_source',
            'prediction_retenue__regle__flux__systeme_destination',
            'prediction_retenue__regle__attribut',
        )
        .order_by('version_validation', 'pk')
    )
    return rows or [validation]


def _validation_target_codes(validation):
    """Resolve destinations safely.

    Accepted predictions are recomputed from the current campaign evidence. The
    historical singular field is used only for explicit/manual decisions or old
    validations that do not have a prediction.
    """
    if validation.decision == ValidationMotif.Decision.ACCEPTE and validation.prediction_retenue_id:
        return prediction_target_codes(validation.prediction_retenue)

    if validation.systeme_a_corriger_final_id:
        return [validation.systeme_a_corriger_final.code_systeme]
    return []


def _target_systems(validation):
    """Resolve the union of systems responsible for every active cause."""
    codes = []
    for cause in _active_validations(validation):
        for code in _validation_target_codes(cause):
            if code not in codes:
                codes.append(code)

    systems_by_code = {
        system.code_systeme: system
        for system in Systeme.objects.filter(code_systeme__in=codes, actif=True)
    }
    systems = [systems_by_code[code] for code in codes if code in systems_by_code]
    if not systems:
        raise RoutingError(
            'Aucun système responsable n’a été confirmé par les preuves du dossier. '
            'Révisez le diagnostic avant d’envoyer une notification.'
        )
    return systems


def _causes_for_system(validation, system):
    """Return only the accepted causes that concern the requested system."""
    causes = []
    for item in _active_validations(validation):
        if system.code_systeme not in _validation_target_codes(item):
            continue

        explanation = item.prediction_retenue.explication or {} if item.prediction_retenue_id else {}
        indices = explanation.get('indices') or []
        local_indices = [
            index for index in indices
            if str(index.get('systeme') or '').strip().upper() == system.code_systeme
        ]
        useful_indices = local_indices or indices
        field = item.motif_final.champ_typique or '-'
        if useful_indices and useful_indices[0].get('champ'):
            field = useful_indices[0]['champ']
        elif explanation.get('attribut_analyse'):
            field = explanation['attribut_analyse']

        constats = []
        for index in useful_indices:
            constat = str(index.get('constat') or '').strip()
            if constat and constat not in constats:
                constats.append(constat)

        causes.append({
            'validation': item,
            'motif': item.motif_final,
            'field': field,
            'constat': ' ; '.join(constats),
            'score': (
                float(item.prediction_retenue.score_confiance) * 100
                if item.prediction_retenue_id else None
            ),
        })
    return causes


def route_group(validation, system):
    """Route using the first matching cause for this system, then fallback to its active group."""
    for cause in _causes_for_system(validation, system):
        rule = RegleAffectation.objects.filter(
            systeme_a_corriger=system,
            motif=cause['motif'],
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

    causes = _causes_for_system(validation, system)
    primary = causes[0] if causes else {
        'motif': validation.motif_final,
        'field': validation.motif_final.champ_typique or '-',
        'constat': '',
        'score': None,
    }

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

    cause_lines = []
    for cause in causes:
        line = f'- {cause["motif"].libelle} | champ : {cause["field"]}'
        if cause['constat']:
            line += f' | constat : {cause["constat"]}'
        if cause['score'] is not None:
            line += f' | confiance : {cause["score"]:.0f}%'
        cause_lines.append(line)
    if not cause_lines:
        cause_lines.append(f'- {primary["motif"].libelle} | champ : {primary["field"]}')

    body = (
        f'Bonjour,\n\n'
        f'BAM Supervise a détecté une anomalie de synchronisation.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Élément non synchronisé : {element}\n'
        f'{gap_description}\n'
        f'Service : {anomaly.code_service or "-"}\n'
        f'Attribut : {anomaly.attribut.code_attribut if anomaly.attribut_id else "-"}\n'
        f'Motif validé : {primary["motif"].libelle}\n'
        f'Champ source à vérifier : {primary["field"]}\n'
        f'Système à corriger : {system.code_systeme}\n'
        f'Système responsable de la correction : {system.code_systeme}\n\n'
        f'Causes à traiter pour {system.code_systeme} :\n'
        + '\n'.join(cause_lines)
        + '\n\n'
        f'Commentaire : {validation.commentaire or "-"}\n\n'
        f'Merci de vérifier ces données dans votre système. '
        f'La résolution sera contrôlée lors du prochain import.\n\n'
        f'BAM Supervise'
    )
    return subject, body


def _send_one(validation, system):
    group = route_group(validation, system)

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
        error_message = _delivery_error_message(exc)
        with transaction.atomic():
            notification.statut = Notification.Statut.ECHEC
            notification.erreur = error_message[:2000]
            notification.save(update_fields=['nb_tentatives', 'statut', 'erreur'])
            for row in rows:
                row.statut_livraison = NotificationDestinataire.Statut.ECHEC
                row.erreur = error_message[:500]
                row.save(update_fields=['statut_livraison', 'erreur'])
        raise RoutingError(error_message) from exc


def send_validation_email(validation):
    """Send one traceable notification per responsible system.

    When several causes were accepted together, their systems are unioned and each
    email contains only the causes relevant to its destination system.
    """
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

    return sent_notifications[0]
