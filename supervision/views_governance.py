import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import (
    CampagneSupervision,
    ContactGroupe,
    JournalAudit,
    Notification,
    ValidationMotif,
)
from .services.notification_retry import retry_failed_notification


_ACTIVITY_TYPES = {
    'campaigns': 'Campagnes',
    'decisions': 'Décisions',
    'notifications': 'Notifications',
    'collaborators': 'Collaborateurs',
}


def _audit(request, action, entity, entity_id, *, old_values=None, new_values=None):
    JournalAudit.objects.create(
        superviseur=request.user if request.user.is_authenticated else None,
        action=action,
        entite=entity,
        id_entite=entity_id,
        anciennes_valeurs=old_values,
        nouvelles_valeurs=new_values,
        adresse_ip=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
    )


@login_required
def notification_retry(request, notification_id):
    """Retry only the failed destination while preserving the original failed record."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    notification = get_object_or_404(
        Notification.objects.select_related(
            'validation__anomalie',
            'validation__systeme_a_corriger_final',
            'groupe__systeme',
        ),
        pk=notification_id,
    )
    if notification.statut != Notification.Statut.ECHEC:
        messages.info(request, 'Seules les notifications en échec peuvent être relancées.')
        return redirect('notification_list')

    try:
        retried = retry_failed_notification(notification)
        _audit(
            request,
            'RETRY_EMAIL',
            'notification',
            notification.pk,
            old_values={
                'statut': notification.statut,
                'nb_tentatives': notification.nb_tentatives,
                'systeme': notification.groupe.systeme.code_systeme,
            },
            new_values={
                'nouvelle_notification_id': retried.pk,
                'statut': retried.statut,
                'systeme': retried.groupe.systeme.code_systeme,
            },
        )
        messages.success(
            request,
            f'Notification {notification.groupe.systeme.code_systeme} relancée avec succès '
            f'pour l’envoi {notification.validation.anomalie.code_envoi}.',
        )
    except Exception as exc:
        _audit(
            request,
            'RETRY_EMAIL_FAILED',
            'notification',
            notification.pk,
            old_values={
                'statut': notification.statut,
                'nb_tentatives': notification.nb_tentatives,
                'systeme': notification.groupe.systeme.code_systeme,
            },
            new_values={'erreur': str(exc)[:300]},
        )
        messages.error(request, f'Échec de la relance : {exc}')
    return redirect('notification_list')


def _filtered_activity_queryset(request):
    qs = JournalAudit.objects.select_related('superviseur').order_by('-cree_le')

    action = request.GET.get('action', '').strip()
    entity = request.GET.get('entite', '').strip()
    activity_type = request.GET.get('type', '').strip()
    search = request.GET.get('q', '').strip()
    date_from_raw = request.GET.get('date_from', '').strip()
    date_to_raw = request.GET.get('date_to', '').strip()

    if activity_type == 'campaigns':
        qs = qs.filter(entite='campagne_supervision')
    elif activity_type == 'decisions':
        qs = qs.filter(entite='validation_motif')
    elif activity_type == 'notifications':
        qs = qs.filter(Q(entite='notification') | Q(action__icontains='EMAIL'))
    elif activity_type == 'collaborators':
        qs = qs.filter(entite='contact_groupe')
    else:
        activity_type = ''

    if action:
        qs = qs.filter(action=action)
    if entity:
        qs = qs.filter(entite=entity)
    if search:
        validation_ids = ValidationMotif.objects.filter(
            anomalie__code_envoi__icontains=search
        ).values_list('pk', flat=True)
        notification_ids = Notification.objects.filter(
            validation__anomalie__code_envoi__icontains=search
        ).values_list('pk', flat=True)
        campaign_ids = CampagneSupervision.objects.filter(
            anomalies__code_envoi__icontains=search
        ).values_list('pk', flat=True)
        contact_ids = ContactGroupe.objects.filter(
            Q(nom_complet__icontains=search)
            | Q(email__icontains=search)
            | Q(groupe__systeme__code_systeme__icontains=search)
        ).values_list('pk', flat=True)

        search_filters = (
            Q(entite__icontains=search)
            | Q(action__icontains=search)
            | Q(superviseur__username__icontains=search)
            | Q(superviseur__nom_complet__icontains=search)
            | Q(entite='validation_motif', id_entite__in=validation_ids)
            | Q(entite='notification', id_entite__in=notification_ids)
            | Q(entite='campagne_supervision', id_entite__in=campaign_ids)
            | Q(entite='contact_groupe', id_entite__in=contact_ids)
        )
        if search.isdigit():
            search_filters |= Q(id_entite=int(search))
        qs = qs.filter(search_filters)

    date_from = parse_date(date_from_raw) if date_from_raw else None
    date_to = parse_date(date_to_raw) if date_to_raw else None
    if date_from:
        qs = qs.filter(cree_le__date__gte=date_from)
    if date_to:
        qs = qs.filter(cree_le__date__lte=date_to)

    filters = {
        'action': action,
        'entite': entity,
        'type': activity_type,
        'q': search,
        'date_from': date_from_raw,
        'date_to': date_to_raw,
    }
    return qs, filters


def _activity_csv_response(qs):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="bam-supervise-activites.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Date', 'Heure', 'Action', 'Entité', 'ID', 'Superviseur', 'Adresse IP', 'Avant', 'Après'])
    for activity in qs.iterator(chunk_size=500):
        local_dt = timezone.localtime(activity.cree_le)
        supervisor = ''
        if activity.superviseur:
            supervisor = activity.superviseur.nom_complet or activity.superviseur.username
        writer.writerow([
            local_dt.strftime('%d/%m/%Y'),
            local_dt.strftime('%H:%M:%S'),
            activity.action,
            activity.entite,
            activity.id_entite,
            supervisor or 'Système',
            activity.adresse_ip or '',
            activity.anciennes_valeurs or '',
            activity.nouvelles_valeurs or '',
        ])
    return response


def _business_activity_maps(activities):
    ids = {
        'validation_motif': set(),
        'notification': set(),
        'campagne_supervision': set(),
        'contact_groupe': set(),
    }
    for activity in activities:
        if activity.entite in ids:
            ids[activity.entite].add(activity.id_entite)

    validations = {
        item.pk: item
        for item in ValidationMotif.objects.filter(pk__in=ids['validation_motif']).select_related(
            'anomalie__attribut',
            'anomalie__systeme_ecart',
            'motif_final',
            'systeme_a_corriger_final',
        )
    }
    notifications = {
        item.pk: item
        for item in Notification.objects.filter(pk__in=ids['notification']).select_related(
            'validation__anomalie__attribut',
            'validation__motif_final',
            'groupe__systeme',
        ).prefetch_related('destinataires')
    }
    campaigns = {
        item.pk: item
        for item in CampagneSupervision.objects.filter(pk__in=ids['campagne_supervision']).prefetch_related(
            'imports__systeme'
        )
    }
    contacts = {
        item.pk: item
        for item in ContactGroupe.objects.filter(pk__in=ids['contact_groupe']).select_related(
            'groupe__systeme'
        )
    }
    return validations, notifications, campaigns, contacts


def _decorate_activity(activity, maps):
    validations, notifications, campaigns, contacts = maps
    activity.ui_tone = 'neutral'
    activity.ui_icon = 'bi-journal-text'
    activity.ui_category = 'Activité'
    activity.ui_title = {
        'CREATE': 'Création enregistrée',
        'UPDATE': 'Modification enregistrée',
        'VALIDATE': 'Décision enregistrée',
        'RETRY_EMAIL': 'Notification relancée',
        'RETRY_EMAIL_FAILED': 'Échec de la relance',
    }.get(activity.action, activity.action.replace('_', ' ').title())
    activity.ui_reference = ''
    activity.ui_description = ''
    activity.ui_meta = []
    activity.ui_url = ''
    activity.ui_url_label = ''

    if activity.entite == 'validation_motif':
        validation = validations.get(activity.id_entite)
        activity.ui_tone = 'decision'
        activity.ui_icon = 'bi-check2'
        activity.ui_category = 'Décision'
        if validation:
            anomaly = validation.anomalie
            activity.ui_reference = anomaly.code_envoi
            activity.ui_url = reverse('anomaly_detail', args=[anomaly.pk])
            activity.ui_url_label = 'Voir le dossier'
            target = validation.systeme_a_corriger_final
            if anomaly.niveau == 'SERVICE':
                activity.ui_title = 'Service non synchronisé validé'
                activity.ui_description = f'Service {anomaly.code_service} confirmé comme absent ou non synchronisé.'
            elif anomaly.niveau == 'ATTRIBUT':
                activity.ui_title = 'Diagnostic attribut validé'
                attribute = anomaly.attribut.code_attribut if anomaly.attribut_id else 'Attribut'
                motif = validation.motif_final.libelle if validation.motif_final_id else 'Diagnostic confirmé'
                activity.ui_description = f'{attribute} · {motif}'
            else:
                activity.ui_title = 'Diagnostic envoi validé'
                motif = validation.motif_final.libelle if validation.motif_final_id else 'Diagnostic confirmé'
                activity.ui_description = motif
            if anomaly.code_service:
                activity.ui_meta.append(f'Service {anomaly.code_service}')
            if target:
                activity.ui_meta.append(f'Système {target.code_systeme}')

    elif activity.entite == 'notification' or 'EMAIL' in activity.action:
        notification = notifications.get(activity.id_entite)
        activity.ui_tone = 'mail'
        activity.ui_icon = 'bi-envelope-paper'
        activity.ui_category = 'Notification'
        activity.ui_title = 'Notification relancée' if activity.action == 'RETRY_EMAIL' else (
            'Échec de la relance' if activity.action == 'RETRY_EMAIL_FAILED' else 'Notification'
        )
        if notification:
            anomaly = notification.validation.anomalie
            activity.ui_reference = anomaly.code_envoi
            activity.ui_url = reverse('anomaly_detail', args=[anomaly.pk])
            activity.ui_url_label = 'Voir le dossier'
            system = notification.groupe.systeme.code_systeme
            recipients = len(notification.destinataires.all())
            if activity.action == 'RETRY_EMAIL_FAILED':
                activity.ui_description = f'La nouvelle tentative vers {system} n’a pas abouti.'
            else:
                activity.ui_description = f'Notification vers {system} pour le traitement de l’anomalie.'
            activity.ui_meta.append(f'{recipients} destinataire' + ('s' if recipients != 1 else ''))
            activity.ui_meta.append(f'Système {system}')

    elif activity.entite == 'campagne_supervision':
        campaign = campaigns.get(activity.id_entite)
        activity.ui_tone = 'campaign'
        activity.ui_icon = 'bi-cloud-check'
        activity.ui_category = 'Campagne'
        activity.ui_title = 'Campagne enregistrée'
        activity.ui_url = reverse('campaign_list')
        activity.ui_url_label = 'Voir les campagnes'
        if campaign:
            activity.ui_reference = f'Campagne #{campaign.pk}'
            if campaign.statut == CampagneSupervision.Statut.TERMINEE:
                activity.ui_title = 'Campagne terminée'
            elif campaign.statut == CampagneSupervision.Statut.ECHEC:
                activity.ui_title = 'Campagne en échec'
                activity.ui_tone = 'danger'
                activity.ui_icon = 'bi-exclamation-triangle'
            elif campaign.statut == CampagneSupervision.Statut.EN_COURS:
                activity.ui_title = 'Campagne en cours'
            systems = [link.systeme.code_systeme for link in campaign.imports.all()]
            activity.ui_description = (
                f"{' · '.join(systems) if systems else 'Comparaison SMI · SICOM · SIBO'} · "
                f'{campaign.nb_anomalies} anomalie' + ('s' if campaign.nb_anomalies != 1 else '')
            )

    elif activity.entite == 'contact_groupe':
        contact = contacts.get(activity.id_entite)
        activity.ui_tone = 'contact'
        activity.ui_icon = 'bi-person-vcard'
        activity.ui_category = 'Collaborateur'
        activity.ui_title = 'Collaborateur ajouté' if activity.action == 'CREATE' else 'Collaborateur modifié'
        activity.ui_url = reverse('group_list')
        activity.ui_url_label = 'Voir les groupes'
        if contact:
            activity.ui_reference = contact.nom_complet
            activity.ui_description = contact.email
            activity.ui_meta.append(f'Système {contact.groupe.systeme.code_systeme}')
            activity.ui_meta.append(contact.groupe.nom_groupe)

    local_dt = timezone.localtime(activity.cree_le)
    activity.ui_local_date = local_dt.date()
    activity.ui_local_time = local_dt.time()
    return activity


def _group_activities_by_day(activities):
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    groups = []
    current = None
    for activity in activities:
        activity_date = activity.ui_local_date
        if current is None or current['date'] != activity_date:
            current = {
                'date': activity_date,
                'is_today': activity_date == today,
                'is_yesterday': activity_date == yesterday,
                'events': [],
            }
            groups.append(current)
        current['events'].append(activity)
    return groups


@login_required
def activity_list(request):
    """Supervisor-facing business journal backed by the immutable audit trail."""
    qs, filters = _filtered_activity_queryset(request)

    if request.GET.get('export') == 'csv':
        return _activity_csv_response(qs)

    actions = list(
        JournalAudit.objects.order_by('action')
        .values_list('action', flat=True)
        .distinct()
    )
    entities = list(
        JournalAudit.objects.order_by('entite')
        .values_list('entite', flat=True)
        .distinct()
    )

    filtered_count = qs.count()
    paginator = Paginator(qs, 40)
    page_obj = paginator.get_page(request.GET.get('page'))
    activities = list(page_obj.object_list)
    maps = _business_activity_maps(activities)
    decorated = [_decorate_activity(activity, maps) for activity in activities]
    activity_groups = _group_activities_by_day(decorated)

    query_without_page = request.GET.copy()
    query_without_page.pop('page', None)
    query_without_page.pop('export', None)

    query_without_type = request.GET.copy()
    query_without_type.pop('page', None)
    query_without_type.pop('export', None)
    query_without_type.pop('type', None)

    context = {
        'activities': decorated,
        'activity_groups': activity_groups,
        'activity_types': _ACTIVITY_TYPES,
        'page_obj': page_obj,
        'actions': actions,
        'entities': entities,
        'filters': filters,
        'total_audit': filtered_count,
        'query_without_page': query_without_page.urlencode(),
        'query_without_type': query_without_type.urlencode(),
    }
    return render(request, 'supervision/activity_journal_page.html', context)
