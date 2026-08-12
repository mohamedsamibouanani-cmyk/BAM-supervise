import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
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
    search = request.GET.get('q', '').strip()
    date_from_raw = request.GET.get('date_from', '').strip()
    date_to_raw = request.GET.get('date_to', '').strip()

    if action:
        qs = qs.filter(action=action)
    if entity:
        qs = qs.filter(entite=entity)
    if search:
        filters = (
            Q(entite__icontains=search)
            | Q(action__icontains=search)
            | Q(superviseur__username__icontains=search)
            | Q(superviseur__nom_complet__icontains=search)
        )
        if search.isdigit():
            filters |= Q(id_entite=int(search))
        qs = qs.filter(filters)

    date_from = parse_date(date_from_raw) if date_from_raw else None
    date_to = parse_date(date_to_raw) if date_to_raw else None
    if date_from:
        qs = qs.filter(cree_le__date__gte=date_from)
    if date_to:
        qs = qs.filter(cree_le__date__lte=date_to)

    filters = {
        'action': action,
        'entite': entity,
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


@login_required
def activity_list(request):
    """Professional supervisor-facing audit consultation with filters and export."""
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

    query_without_page = request.GET.copy()
    query_without_page.pop('page', None)
    query_without_page.pop('export', None)

    today = timezone.localdate()
    context = {
        'activities': page_obj.object_list,
        'page_obj': page_obj,
        'actions': actions,
        'entities': entities,
        'filters': filters,
        'total_audit': filtered_count,
        'today_count': JournalAudit.objects.filter(cree_le__date=today).count(),
        'validation_count': ValidationMotif.objects.count(),
        'notification_sent_count': Notification.objects.filter(statut=Notification.Statut.ENVOYEE).count(),
        'notification_failed_count': Notification.objects.filter(statut=Notification.Statut.ECHEC).count(),
        'collaborator_count': ContactGroupe.objects.count(),
        'campaign_count': CampagneSupervision.objects.count(),
        'query_without_page': query_without_page.urlencode(),
    }
    return render(request, 'supervision/activity_list.html', context)
