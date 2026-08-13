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
from supervision.services.security import decrypt_sensitive


class RoutingError(ValueError):
    pass


def _ensure_real_delivery_backend():
    try:
        validate_real_email_config()
    except EmailConfigurationError as exc:
        raise RoutingError(str(exc)) from exc


def _delivery_error_message(exc):
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
    if validation.decision == ValidationMotif.Decision.ACCEPTE and validation.prediction_retenue_id:
        return prediction_target_codes(validation.prediction_retenue)
    if validation.systeme_a_corriger_final_id:
        return [validation.systeme_a_corriger_final.code_systeme]
    return []


def _target_systems(validation):
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
            'Aucun système responsable ou source n’a été confirmé par le superviseur. '
            'Révisez le diagnostic avant d’envoyer une notification.'
        )
    return systems


def _causes_for_system(validation, system):
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

        role = explanation.get('role_diagnostic')
        causes.append({
            'validation': item,
            'motif': item.motif_final,
            'label': explanation.get('message') or item.motif_final.libelle,
            'field': field,
            'constat': ' ; '.join(constats),
            'role': role,
            'score': (
                float(item.prediction_retenue.score_confiance) * 100
                if item.prediction_retenue_id
                and role not in {
                    'MOTIF_NON_IDENTIFIABLE',
                    'CONSTAT_ATTRIBUT',
                    'CONSTAT_ATTRIBUT_DIFFERENT',
                    'CONSTAT_SERVICE',
                }
                else None
            ),
        })
    return causes


def route_group(validation, system):
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


def _service_source_observations(anomaly, target_system):
    observations = []
    links = anomaly.campagne.imports.select_related('systeme', 'fichier_import').order_by(
        'systeme__ordre_comparaison'
    )
    for link in links:
        if link.systeme_id == target_system.pk:
            continue
        shipment = link.fichier_import.envois.filter(code_envoi=anomaly.code_envoi).first()
        if not shipment:
            continue
        service = shipment.services.filter(code_service=anomaly.code_service).order_by('numero_occurrence').first()
        if not service:
            continue
        observations.append({
            'systeme': link.systeme.code_systeme,
            'article': service.code_service,
            'des_article': service.libelle_service or '-',
        })
    return observations


def _build_service_email(validation, system):
    anomaly = validation.anomalie
    observations = _service_source_observations(anomaly, system)
    descriptions = {
        item['des_article'] for item in observations if item['des_article'] not in ('', '-')
    }
    source_lines = [
        f'- {item["systeme"]} : ARTICLE={item["article"]} | DES_ARTICLE={item["des_article"]}'
        for item in observations
    ]
    if len(descriptions) == 1:
        des_article = next(iter(descriptions))
        action = (
            f'Merci de renseigner/synchroniser ce service dans {system.code_systeme} avec '
            f'ARTICLE={anomaly.code_service} et DES_ARTICLE={des_article}, puis de vérifier '
            f'sa prise en compte.'
        )
    else:
        action = (
            f'Merci de renseigner/synchroniser le service ARTICLE={anomaly.code_service} '
            f'dans {system.code_systeme}. Les libellés DES_ARTICLE observés sont indiqués '
            f'ci-dessus ; s’ils diffèrent, vérifiez le libellé métier correct avant saisie.'
        )

    subject = (
        f'[BAM Supervise][{system.code_systeme}] SERVICE À RENSEIGNER - {anomaly.code_envoi}'
    )
    body = (
        f'Bonjour,\n\n'
        f'Après validation du superviseur, BAM Supervise confirme qu’un service '
        f'n’est pas synchronisé dans votre système.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Système où le service manque : {system.code_systeme}\n'
        f'ARTICLE : {anomaly.code_service or "-"}\n'
        f'Constat : service absent / non synchronisé\n\n'
        f'Informations observées dans le ou les systèmes où le service existe :\n'
        + ('\n'.join(source_lines) if source_lines else '- aucune information source disponible')
        + '\n\n'
        + action
        + '\n\n'
        f'Aucun motif métier n’est attribué automatiquement au niveau SERVICE.\n'
        f'La résolution sera contrôlée lors de la prochaine campagne.\n\n'
        f'Commentaire du superviseur : {validation.commentaire or "-"}\n\n'
        f'BAM Supervise'
    )
    return subject, body


def _build_envoi_retry_email(validation, system):
    anomaly = validation.anomalie
    observed = anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '-'
    subject = (
        f'[BAM Supervise][{system.code_systeme}] RELANCE ENVOI - {anomaly.code_envoi}'
    )
    body = (
        f'Bonjour,\n\n'
        f'Après validation du superviseur, BAM Supervise confirme que l’envoi '
        f'{anomaly.code_envoi} n’est pas synchronisé dans {observed}.\n\n'
        f'Le moteur a analysé les champs obligatoires puis les formats disponibles '
        f'sans identifier de cause exploitable.\n'
        f'Motif validé : Motif non identifiable\n'
        f'Système source à relancer : {system.code_systeme}\n\n'
        f'Merci de relancer l’envoi / la synchronisation depuis {system.code_systeme} '
        f'et de vérifier sa prise en compte dans le système cible.\n'
        f'BAM Supervise contrôlera automatiquement le résultat lors de la prochaine campagne : '
        f'l’anomalie sera marquée résolue si l’envoi apparaît, sinon persistante.\n\n'
        f'Commentaire du superviseur : {validation.commentaire or "-"}\n\n'
        f'BAM Supervise'
    )
    return subject, body


def _attribute_observations(anomaly, excluded_system=None):
    """Read the real source values only while building an email after validation.

    Sensitive values remain encrypted in the database. They are decrypted in memory
    here so the collaborator receives the exact value that must be corrected or filled.
    """
    observations = []
    if not anomaly.attribut_id:
        return observations

    links = anomaly.campagne.imports.select_related('systeme', 'fichier_import').order_by(
        'systeme__ordre_comparaison'
    )
    for link in links:
        if excluded_system is not None and link.systeme_id == excluded_system.pk:
            continue
        shipment = link.fichier_import.envois.filter(code_envoi=anomaly.code_envoi).first()
        if not shipment:
            continue

        value = None
        if anomaly.attribut.portee == 'ENVOI':
            value = shipment.valeurs_attribut.filter(attribut=anomaly.attribut).first()
        else:
            service = shipment.services.filter(
                code_service=anomaly.code_service
            ).order_by('numero_occurrence').first()
            if service:
                value = service.valeurs_attribut.filter(attribut=anomaly.attribut).first()

        if value is None or value.est_vide:
            continue

        raw = value.valeur_brute or ''
        display_value = decrypt_sensitive(raw) if anomaly.attribut.sensible else str(raw).strip()
        observations.append({
            'systeme': link.systeme.code_systeme,
            'valeur': display_value or '—',
            'comparison_value': display_value or '',
        })
    return observations


def _attribute_value_for_system(anomaly, system):
    for item in _attribute_observations(anomaly):
        if item['systeme'] == system.code_systeme:
            return item['valeur']
    return '—'


def _is_neutral_attribute_absence(validation):
    anomaly = validation.anomalie
    if (
        anomaly.niveau != Anomalie.Niveau.ATTRIBUT
        or anomaly.type_ecart != Anomalie.TypeEcart.ABSENT
    ):
        return False
    if validation.motif_final.code_motif == 'ATTRIBUT_NON_SYNCHRONISE':
        return True
    if validation.prediction_retenue_id:
        explanation = validation.prediction_retenue.explication or {}
        return explanation.get('role_diagnostic') == 'CONSTAT_ATTRIBUT'
    return False


def _is_attribute_format_validation(validation):
    if validation.anomalie.niveau != Anomalie.Niveau.ATTRIBUT:
        return False
    if validation.motif_final.code_motif in {
        'FORMAT_MONTANT_INCOMPATIBLE', 'FORMAT_ATTRIBUT_INCOMPATIBLE'
    }:
        return True
    if validation.prediction_retenue_id:
        explanation = validation.prediction_retenue.explication or {}
        indices = explanation.get('indices') or []
        return any(
            'format' in str(index.get('constat') or '').lower()
            for index in indices
        )
    return False


def _is_neutral_attribute_difference(validation):
    anomaly = validation.anomalie
    if (
        anomaly.niveau != Anomalie.Niveau.ATTRIBUT
        or anomaly.type_ecart != Anomalie.TypeEcart.DIFFERENT
    ):
        return False
    if validation.prediction_retenue_id:
        explanation = validation.prediction_retenue.explication or {}
        return explanation.get('role_diagnostic') == 'CONSTAT_ATTRIBUT_DIFFERENT'
    return validation.motif_final.code_motif == 'ATTRIBUT_DIFFERENT'


def _build_attribute_fill_email(validation, system):
    anomaly = validation.anomalie
    field = anomaly.attribut.code_attribut if anomaly.attribut_id else 'ATTRIBUT'
    observations = _attribute_observations(anomaly, excluded_system=system)
    source_lines = [f'- {item["systeme"]} : {item["valeur"]}' for item in observations]

    exact_values = {
        item['comparison_value']
        for item in observations
        if item['comparison_value'] not in ('', '—')
    }

    if len(observations) == 1:
        reference = observations[0]['valeur']
        instruction = (
            f'Merci de renseigner l’attribut {field} dans {system.code_systeme} '
            f'avec la valeur observée dans le système source : {reference}.'
        )
    elif observations and len(exact_values) == 1:
        reference = observations[0]['valeur']
        instruction = (
            f'Les systèmes sources présentent la même valeur. Merci de renseigner '
            f'l’attribut {field} dans {system.code_systeme} avec la valeur : {reference}.'
        )
    elif observations:
        instruction = (
            f'Les systèmes sources présentent des valeurs différentes. BAM Supervise '
            f'ne choisit pas automatiquement une valeur de référence. Merci de vérifier '
            f'la valeur métier correcte avant de renseigner l’attribut {field} dans '
            f'{system.code_systeme}.'
        )
    else:
        instruction = (
            f'Aucune valeur source exploitable n’a été retrouvée dans le dossier. '
            f'Merci de vérifier la donnée source avant de renseigner l’attribut {field} '
            f'dans {system.code_systeme}.'
        )

    subject = (
        f'[BAM Supervise][{system.code_systeme}] ATTRIBUT À RENSEIGNER - {anomaly.code_envoi}'
    )
    body = (
        f'Bonjour,\n\n'
        f'Après validation du superviseur, BAM Supervise confirme qu’un attribut '
        f'n’est pas synchronisé dans votre système.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Service : {anomaly.code_service or "-"}\n'
        f'Attribut : {field}\n'
        f'Système où l’attribut manque : {system.code_systeme}\n'
        f'Diagnostic validé : Attribut non synchronisé\n'
        f'Aucun motif de format n’a été identifié.\n\n'
        f'Valeur(s) observée(s) dans le ou les systèmes sources :\n'
        + ('\n'.join(source_lines) if source_lines else '- aucune valeur source disponible')
        + '\n\n'
        + instruction
        + '\n\n'
        f'BAM Supervise contrôlera le résultat lors de la prochaine campagne : '
        f'l’anomalie sera marquée résolue si l’attribut est désormais présent, '
        f'sinon persistante.\n\n'
        f'Commentaire du superviseur : {validation.commentaire or "-"}\n\n'
        f'BAM Supervise'
    )
    return subject, body


def _build_attribute_format_email(validation, system):
    anomaly = validation.anomalie
    field = anomaly.attribut.code_attribut if anomaly.attribut_id else 'ATTRIBUT'
    causes = _causes_for_system(validation, system)
    constats = [cause['constat'] for cause in causes if cause['constat']]
    detail = ' ; '.join(dict.fromkeys(constats)) or 'Format source non conforme'
    observed_value = _attribute_value_for_system(anomaly, system)

    subject = (
        f'[BAM Supervise][{system.code_systeme}] FORMAT ATTRIBUT À CORRIGER - {anomaly.code_envoi}'
    )
    body = (
        f'Bonjour,\n\n'
        f'Après validation du superviseur, BAM Supervise a identifié un problème de '
        f'format sur un attribut dans votre système source.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Service : {anomaly.code_service or "-"}\n'
        f'Attribut : {field}\n'
        f'Système source à corriger : {system.code_systeme}\n'
        f'Valeur observée : {observed_value}\n'
        f'Constat : {detail}\n\n'
        f'Merci de corriger le format du champ {field} dans {system.code_systeme}, '
        f'puis de relancer sa synchronisation vers les autres systèmes.\n'
        f'La résolution sera contrôlée lors de la prochaine campagne.\n\n'
        f'Commentaire du superviseur : {validation.commentaire or "-"}\n\n'
        f'BAM Supervise'
    )
    return subject, body


def _build_attribute_difference_email(validation, system):
    anomaly = validation.anomalie
    field = anomaly.attribut.code_attribut if anomaly.attribut_id else 'ATTRIBUT'
    observations = _attribute_observations(anomaly)
    value_lines = [
        f'- {item["systeme"]} : {item["valeur"]}'
        for item in observations
    ]
    subject = (
        f'[BAM Supervise][{system.code_systeme}] VALEUR ATTRIBUT À VÉRIFIER - {anomaly.code_envoi}'
    )
    body = (
        f'Bonjour,\n\n'
        f'Après validation du superviseur, BAM Supervise confirme que les systèmes '
        f'ne portent pas la même valeur pour un attribut.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Service : {anomaly.code_service or "-"}\n'
        f'Attribut : {field}\n'
        f'Système choisi par le superviseur pour correction : {system.code_systeme}\n\n'
        f'Valeurs observées :\n'
        + ('\n'.join(value_lines) if value_lines else '- aucune valeur disponible')
        + '\n\n'
        f'Aucune règle métier n’est encore configurée pour déterminer automatiquement '
        f'la valeur de référence. Le système à traiter a donc été confirmé par le superviseur.\n'
        f'Merci de vérifier la valeur métier de référence puis de mettre à jour '
        f'{system.code_systeme} conformément à la décision validée.\n'
        f'La cohérence sera contrôlée lors de la prochaine campagne.\n\n'
        f'Commentaire du superviseur : {validation.commentaire or "-"}\n\n'
        f'BAM Supervise'
    )
    return subject, body


def build_email(validation, group, system):
    anomaly = validation.anomalie
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return _build_service_email(validation, system)

    if (
        anomaly.niveau == Anomalie.Niveau.ENVOI
        and validation.motif_final.code_motif == 'MOTIF_INCONNU'
    ):
        return _build_envoi_retry_email(validation, system)

    if _is_attribute_format_validation(validation):
        return _build_attribute_format_email(validation, system)

    if _is_neutral_attribute_absence(validation):
        return _build_attribute_fill_email(validation, system)

    if _is_neutral_attribute_difference(validation):
        return _build_attribute_difference_email(validation, system)

    if anomaly.niveau == Anomalie.Niveau.ENVOI:
        element = f'Envoi {anomaly.code_envoi}'
    else:
        element = (
            f'Attribut {anomaly.attribut.code_attribut if anomaly.attribut_id else "-"} '
            f'de l’envoi {anomaly.code_envoi}'
        )

    causes = _causes_for_system(validation, system)
    primary = causes[0] if causes else {
        'motif': validation.motif_final,
        'label': validation.motif_final.libelle,
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
        line = f'- {cause["label"]} | champ : {cause["field"]}'
        if cause['constat']:
            line += f' | constat : {cause["constat"]}'
        if cause['score'] is not None:
            line += f' | confiance : {cause["score"]:.0f}%'
        cause_lines.append(line)
    if not cause_lines:
        cause_lines.append(f'- {primary["label"]} | champ : {primary["field"]}')

    body = (
        f'Bonjour,\n\n'
        f'BAM Supervise a détecté une anomalie de synchronisation.\n\n'
        f'Code envoi : {anomaly.code_envoi}\n'
        f'Élément non synchronisé : {element}\n'
        f'{gap_description}\n'
        f'Service : {anomaly.code_service or "-"}\n'
        f'Attribut : {anomaly.attribut.code_attribut if anomaly.attribut_id else "-"}\n'
        f'Motif validé : {primary["label"]}\n'
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
                'Notifications envoyées aux systèmes responsables ou sources validés : '
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