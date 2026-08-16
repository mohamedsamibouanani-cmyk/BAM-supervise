from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render

from .models import Notification


@login_required
def notification_list(request):
    """Supervisor notification journal with bounded, query-efficient loading."""
    qs = (
        Notification.objects.select_related(
            'groupe__systeme',
            'validation__anomalie',
            'validation__motif_final',
            'validation__systeme_a_corriger_final',
        )
        .annotate(recipient_count=Count('destinataires', distinct=True))
        .order_by('-creee_le')
    )

    statut = request.GET.get('statut', '').strip()
    valid_statuses = {value for value, _label in Notification.Statut.choices}
    if statut in valid_statuses:
        qs = qs.filter(statut=statut)
    else:
        statut = ''

    context = {
        'notifications': qs[:250],
        'statut': statut,
        'sent_count': Notification.objects.filter(statut=Notification.Statut.ENVOYEE).count(),
        'pending_count': Notification.objects.filter(statut=Notification.Statut.A_ENVOYER).count(),
        'failed_count': Notification.objects.filter(statut=Notification.Statut.ECHEC).count(),
    }
    return render(request, 'supervision/notification_list.html', context)
