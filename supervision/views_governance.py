from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from .models import ContactGroupe, JournalAudit


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
def contact_toggle(request, contact_id):
    """Archive/reactivate a notification contact without deleting audit history."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    contact = get_object_or_404(
        ContactGroupe.objects.select_related('groupe__systeme'),
        pk=contact_id,
    )
    previous = contact.actif
    contact.actif = not contact.actif
    contact.save(update_fields=['actif'])

    _audit(
        request,
        'ACTIVATE' if contact.actif else 'DEACTIVATE',
        'contact_groupe',
        contact.pk,
        old_values={'actif': previous, 'email': contact.email},
        new_values={'actif': contact.actif, 'email': contact.email},
    )
    state = 'réactivé' if contact.actif else 'désactivé'
    messages.success(request, f'Collaborateur {contact.nom_complet} {state}.')
    return redirect('group_list')


@login_required
def activity_list(request):
    """Supervisor-facing immutable-style audit consultation view."""
    qs = JournalAudit.objects.select_related('superviseur').order_by('-cree_le')

    action = request.GET.get('action', '').strip()
    entity = request.GET.get('entite', '').strip()
    search = request.GET.get('q', '').strip()

    if action:
        qs = qs.filter(action=action)
    if entity:
        qs = qs.filter(entite=entity)
    if search:
        filters = Q(entite__icontains=search) | Q(action__icontains=search)
        if search.isdigit():
            filters |= Q(id_entite=int(search))
        qs = qs.filter(filters)

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

    return render(request, 'supervision/activity_list.html', {
        'activities': qs[:500],
        'actions': actions,
        'entities': entities,
        'filters': {'action': action, 'entite': entity, 'q': search},
        'total_audit': JournalAudit.objects.count(),
    })
