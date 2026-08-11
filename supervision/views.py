from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify

from .forms import CampaignImportForm, ContactGroupeForm, ValidationMotifForm
from .models import (
    Anomalie, CampagneImport, CampagneSupervision, ContactGroupe, ExempleApprentissage,
    GroupeResponsable, HistoriqueAnomalie, JournalAudit, Motif, Notification, Systeme,
    ValidationMotif,
)
from .services.comparison import run_campaign
from .services.importer import import_excel
from .services.notifications import send_validation_email


def _audit(request, action, entity, entity_id, new_values=None, old_values=None):
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
def dashboard(request):
    anomalies = Anomalie.objects.all()
    total = anomalies.count()
    resolues = anomalies.filter(statut=Anomalie.Statut.RESOLUE).count()
    ouvertes = anomalies.exclude(statut=Anomalie.Statut.RESOLUE).count()
    a_valider = anomalies.filter(statut__in=[Anomalie.Statut.DETECTEE, Anomalie.Statut.ANALYSEE]).count()
    en_suivi = anomalies.filter(
        statut__in=[Anomalie.Statut.VALIDEE, Anomalie.Statut.NOTIFIEE, Anomalie.Statut.PERSISTANTE]
    ).count()
    taux_resolution = round((resolues / total * 100), 1) if total else 0

    level_counts = {row['niveau']: row['total'] for row in anomalies.values('niveau').annotate(total=Count('id'))}
    today = timezone.localdate()
    first_day = today - timedelta(days=6)
    nouvelles_aujourdhui = anomalies.filter(detectee_le__date=today).count()
    detected_trend_rows = (
        anomalies.filter(detectee_le__date__gte=first_day)
        .annotate(day=TruncDate('detectee_le'))
        .values('day').annotate(total=Count('id')).order_by('day')
    )
    resolved_trend_rows = (
        anomalies.filter(cloturee_le__date__gte=first_day, cloturee_le__date__lte=today)
        .annotate(day=TruncDate('cloturee_le'))
        .values('day').annotate(total=Count('id')).order_by('day')
    )
    detected_trend_map = {row['day']: row['total'] for row in detected_trend_rows}
    resolved_trend_map = {row['day']: row['total'] for row in resolved_trend_rows}
    trend_days = [first_day + timedelta(days=i) for i in range(7)]
    detected_trend_values = [detected_trend_map.get(day, 0) for day in trend_days]
    resolved_trend_values = [resolved_trend_map.get(day, 0) for day in trend_days]
    trend_detected_total = sum(detected_trend_values)
    trend_resolved_total = sum(resolved_trend_values)

    dashboard_data = {
        'trend': {
            'labels': [day.strftime('%d/%m') for day in trend_days],
            'detected': detected_trend_values,
            'resolved': resolved_trend_values,
        },
    }

    campaign_queryset = CampagneSupervision.objects.annotate(
        import_count=Count('imports', distinct=True),
    ).order_by('-demarree_le')
    latest_campaign = campaign_queryset.first()
    context = {
        'total': total,
        'ouvertes': ouvertes,
        'resolues': resolues,
        'a_valider': a_valider,
        'en_suivi': en_suivi,
        'nouvelles_aujourdhui': nouvelles_aujourdhui,
        'taux_resolution': taux_resolution,
        'trend_detected_total': trend_detected_total,
        'trend_resolved_total': trend_resolved_total,
        'trend_balance': trend_detected_total - trend_resolved_total,
        'trend_has_activity': bool(trend_detected_total or trend_resolved_total),
        'campagnes': campaign_queryset[:4],
        'campagnes_echec': CampagneSupervision.objects.filter(statut=CampagneSupervision.Statut.ECHEC).count(),
        'latest_campaign': latest_campaign,
        'prioritaires': (
            anomalies.filter(statut__in=[Anomalie.Statut.DETECTEE, Anomalie.Statut.ANALYSEE])
            .select_related('systeme_ecart', 'attribut')
            .order_by('detectee_le')[:5]
        ),
        'notifications_envoyees': Notification.objects.filter(statut=Notification.Statut.ENVOYEE).count(),
        'notifications_echec': Notification.objects.filter(statut=Notification.Statut.ECHEC).count(),
        'level_counts': {
            'envoi': level_counts.get('ENVOI', 0),
            'service': level_counts.get('SERVICE', 0),
            'attribut': level_counts.get('ATTRIBUT', 0),
        },
        'dashboard_data': dashboard_data,
    }
    return render(request, 'supervision/dashboard.html', context)


@login_required
def campaign_list(request):
    all_campaigns = CampagneSupervision.objects.all()
    summary = {
        'total': all_campaigns.count(),
        'terminees': all_campaigns.filter(statut=CampagneSupervision.Statut.TERMINEE).count(),
        'echecs': all_campaigns.filter(statut=CampagneSupervision.Statut.ECHEC).count(),
        'en_cours': all_campaigns.filter(statut=CampagneSupervision.Statut.EN_COURS).count(),
    }
    qs = (
        all_campaigns.annotate(
            import_count=Count('imports', distinct=True),
            anomaly_count=Count('anomalies', distinct=True),
        )
        .select_related('superviseur')
        .order_by('-demarree_le')
    )
    statut = request.GET.get('statut', '').strip()
    valid_statuses = {value for value, _label in CampagneSupervision.Statut.choices}
    if statut in valid_statuses:
        qs = qs.filter(statut=statut)
    else:
        statut = ''
    return render(
        request,
        'supervision/campaign_list.html',
        {'campagnes': qs[:250], 'statut': statut, 'summary': summary},
    )


@login_required
def campaign_create(request):
    if request.method == 'POST':
        form = CampaignImportForm(request.POST, request.FILES)
        if form.is_valid():
            systems = {s.code_systeme: s for s in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])}
            if len(systems) != 3:
                messages.error(request, 'Initialisez les systèmes SMI, SICOM et SIBO avec seed_bam.')
                return redirect('campaign_create')
            campaign = CampagneSupervision.objects.create(
                superviseur=request.user,
                statut=CampagneSupervision.Statut.EN_COURS,
            )
            current_system = None
            try:
                uploaded = {
                    'SMI': form.cleaned_data['fichier_smi'],
                    'SICOM': form.cleaned_data['fichier_sicom'],
                    'SIBO': form.cleaned_data['fichier_sibo'],
                }
                for code, file_obj in uploaded.items():
                    current_system = code
                    fimport = import_excel(file_obj, systems[code], request.user)
                    CampagneImport.objects.create(campagne=campaign, systeme=systems[code], fichier_import=fimport)
                current_system = None
                run_campaign(campaign)
                _audit(request, 'CREATE', 'campagne_supervision', campaign.pk, {'nb_anomalies': campaign.nb_anomalies})
                messages.success(
                    request,
                    'Import réussi. Les trois fichiers ont été validés et la comparaison est terminée. '
                    f'{campaign.nb_anomalies} anomalie(s) détectée(s).',
                )
                return redirect('anomaly_list')
            except Exception as exc:
                campaign.statut = CampagneSupervision.Statut.ECHEC
                campaign.terminee_le = timezone.now()
                campaign.message_erreur = str(exc)
                campaign.save(update_fields=['statut', 'terminee_le', 'message_erreur'])
                if current_system:
                    reason = f'le fichier {current_system} n’a pas pu être validé : {exc}'
                else:
                    reason = f'la comparaison n’a pas pu être terminée : {exc}'
                messages.error(request, f'Import impossible : {reason}')
                # Browsers never repopulate file inputs after a response. Return
                # a fresh form so the visual state matches what can be submitted.
                form = CampaignImportForm()
    else:
        form = CampaignImportForm()
    return render(request, 'supervision/campaign_form.html', {'form': form})


@login_required
def anomaly_list(request):
    all_anomalies = Anomalie.objects.all()
    action_statuses = [Anomalie.Statut.DETECTEE, Anomalie.Statut.ANALYSEE]
    follow_up_statuses = [Anomalie.Statut.VALIDEE, Anomalie.Statut.NOTIFIEE, Anomalie.Statut.PERSISTANTE]
    summary = {
        'total': all_anomalies.count(),
        'a_traiter': all_anomalies.filter(statut__in=action_statuses).count(),
        'en_suivi': all_anomalies.filter(statut__in=follow_up_statuses).count(),
        'resolues': all_anomalies.filter(statut=Anomalie.Statut.RESOLUE).count(),
    }

    qs = all_anomalies.select_related('systeme_ecart', 'attribut', 'campagne')
    vue = request.GET.get('vue', '').strip()
    if vue == 'a_traiter':
        qs = qs.filter(statut__in=action_statuses)
    elif vue == 'en_suivi':
        qs = qs.filter(statut__in=follow_up_statuses)
    elif vue == 'resolues':
        qs = qs.filter(statut=Anomalie.Statut.RESOLUE)
    else:
        vue = ''

    for field in ('niveau', 'type_ecart', 'statut'):
        value = request.GET.get(field, '').strip()
        valid_values = {choice_value for choice_value, _label in Anomalie._meta.get_field(field).choices}
        if value in valid_values:
            qs = qs.filter(**{field: value})
    system = request.GET.get('systeme', '').strip()
    if system:
        qs = qs.filter(systeme_ecart__code_systeme=system)
    search = request.GET.get('q', '').strip()
    if search:
        qs = qs.filter(
            Q(code_envoi__icontains=search)
            | Q(code_service__icontains=search)
            | Q(attribut__code_attribut__icontains=search)
        )

    qs = qs.annotate(
        work_priority=Case(
            When(statut__in=action_statuses, then=Value(0)),
            When(statut__in=follow_up_statuses, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by('work_priority', 'detectee_le')

    filtered_total = qs.count()
    context = {
        'anomalies': qs[:250],
        'systems': Systeme.objects.all(),
        'summary': summary,
        'filtered_total': filtered_total,
        'filters': request.GET,
        'vue': vue,
    }
    return render(request, 'supervision/anomaly_list.html', context)


@login_required
def anomaly_detail(request, pk):
    anomaly = get_object_or_404(
        Anomalie.objects.select_related('systeme_ecart', 'attribut', 'campagne').prefetch_related(
            'details__systeme',
            'predictions__motif',
            'predictions__systeme_a_corriger_predit',
            'validations__motif_final',
            'validations__systeme_a_corriger_final',
            'historique',
            'verifications__campagne_controle',
        ),
        pk=pk,
    )
    latest_validation = (
        anomaly.validations.filter(est_finale=True)
        .select_related('motif_final', 'systeme_a_corriger_final', 'prediction_retenue')
        .order_by('-validee_le')
        .first()
    )
    notifications = []
    if latest_validation:
        notifications = list(latest_validation.notifications.select_related('groupe').prefetch_related('destinataires').order_by('-creee_le'))
    return render(request, 'supervision/anomaly_detail.html', {
        'anomaly': anomaly,
        'latest_validation': latest_validation,
        'notifications': notifications,
    })


@login_required
@transaction.atomic
def anomaly_validate(request, pk):
    anomaly = get_object_or_404(Anomalie, pk=pk)
    if request.method == 'POST':
        form = ValidationMotifForm(anomaly, request.POST)
        if form.is_valid():
            anomaly.validations.filter(est_finale=True).update(est_finale=False)
            version = (anomaly.validations.order_by('-version_validation').values_list('version_validation', flat=True).first() or 0) + 1
            prediction = form.cleaned_data.get('prediction')
            motif_final = form.cleaned_data.get('motif_final')
            decision = form.cleaned_data['decision']
            nouveau_motif = (form.cleaned_data.get('nouveau_motif') or '').strip()
            if nouveau_motif:
                motif_final = Motif.objects.filter(libelle__iexact=nouveau_motif).first()
                if not motif_final:
                    base_code = slugify(nouveau_motif).replace('-', '_').upper()[:60] or 'MOTIF_APPRIS'
                    code = base_code
                    suffix = 2
                    while Motif.objects.filter(code_motif=code).exists():
                        code = f'{base_code[:70]}_{suffix}'
                        suffix += 1
                    motif_final = Motif.objects.create(
                        code_motif=code,
                        libelle=nouveau_motif,
                        description='Motif ajouté après validation humaine dans un dossier de supervision.',
                        niveau_applicable=anomaly.niveau,
                        categorie='APPRENTISSAGE',
                        actif=True,
                    )
                decision = ValidationMotif.Decision.MODIFIE
            validation = ValidationMotif.objects.create(
                anomalie=anomaly,
                prediction_retenue=prediction,
                motif_final=motif_final,
                systeme_a_corriger_final=form.cleaned_data['systeme_a_corriger_final'],
                superviseur=request.user,
                decision=decision,
                commentaire=form.cleaned_data['commentaire'],
                version_validation=version,
                est_finale=True,
            )
            ExempleApprentissage.objects.create(
                validation=validation,
                motif_label=validation.motif_final,
                systeme_a_corriger_label=validation.systeme_a_corriger_final,
                caracteristiques={
                    'niveau': anomaly.niveau,
                    'type_ecart': anomaly.type_ecart,
                    'service': anomaly.code_service,
                    'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
                    'systeme_ecart': anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
                },
                eligible=validation.decision != ValidationMotif.Decision.INCONNU,
                raison_exclusion='Motif inconnu' if validation.decision == ValidationMotif.Decision.INCONNU else '',
            )
            old = anomaly.statut
            anomaly.statut = Anomalie.Statut.VALIDEE
            anomaly.save(update_fields=['statut'])
            HistoriqueAnomalie.objects.create(
                anomalie=anomaly, ancien_statut=old, nouveau_statut=Anomalie.Statut.VALIDEE,
                source_evenement='SUPERVISEUR', superviseur=request.user,
                commentaire=f'Motif final: {validation.motif_final.libelle}',
            )
            _audit(request, 'VALIDATE', 'validation_motif', validation.pk, {
                'motif': validation.motif_final.code_motif,
                'systeme_a_corriger': validation.systeme_a_corriger_final.code_systeme,
            })
            try:
                send_validation_email(validation)
                messages.success(request, 'Motif validé et e-mail envoyé au groupe responsable.')
            except Exception as exc:
                messages.warning(request, f'Motif validé, mais l’e-mail n’a pas pu être envoyé : {exc}')
            return redirect('anomaly_detail', pk=pk)
    else:
        initial = {}
        prediction = anomaly.predictions.order_by('rang').first()
        if prediction:
            initial['prediction'] = prediction
            initial['motif_final'] = prediction.motif
            initial['systeme_a_corriger_final'] = prediction.systeme_a_corriger_predit or anomaly.systeme_ecart
        form = ValidationMotifForm(anomaly, initial=initial)
    return render(request, 'supervision/validation_form.html', {'form': form, 'anomaly': anomaly})


@login_required
def notification_list(request):
    qs = (
        Notification.objects.select_related(
            'groupe', 'validation__anomalie', 'validation__motif_final', 'validation__systeme_a_corriger_final'
        )
        .annotate(recipient_count=Count('destinataires', distinct=True))
        .order_by('-creee_le')
    )
    statut = request.GET.get('statut', '').strip()
    if statut:
        qs = qs.filter(statut=statut)
    return render(request, 'supervision/notification_list.html', {
        'notifications': qs[:300],
        'statut': statut,
        'sent_count': Notification.objects.filter(statut=Notification.Statut.ENVOYEE).count(),
        'failed_count': Notification.objects.filter(statut=Notification.Statut.ECHEC).count(),
        'pending_count': Notification.objects.filter(statut=Notification.Statut.A_ENVOYER).count(),
    })


@login_required
def group_list(request):
    groups = (
        GroupeResponsable.objects.select_related('systeme')
        .prefetch_related('contacts')
        .order_by('systeme__ordre_comparaison', 'nom_groupe')
    )
    return render(request, 'supervision/group_list.html', {'groups': groups})


@login_required
def contact_add(request, group_id):
    group = get_object_or_404(GroupeResponsable, pk=group_id)
    if request.method == 'POST':
        form = ContactGroupeForm(request.POST)
        if form.is_valid():
            contact = form.save(commit=False)
            contact.groupe = group
            contact.save()
            _audit(request, 'CREATE', 'contact_groupe', contact.pk, {
                'email': contact.email,
                'nom': contact.nom_complet,
                'fonction': contact.fonction,
                'systeme': group.systeme.code_systeme,
            })
            messages.success(request, f'Adresse {contact.email} ajoutée au groupe {group.nom_groupe}.')
            return redirect('group_list')
    else:
        form = ContactGroupeForm()
    return render(request, 'supervision/contact_form.html', {'form': form, 'group': group, 'mode': 'create'})


@login_required
def contact_edit(request, contact_id):
    contact = get_object_or_404(ContactGroupe.objects.select_related('groupe__systeme'), pk=contact_id)
    group = contact.groupe
    before = {
        'nom_complet': contact.nom_complet,
        'email': contact.email,
        'fonction': contact.fonction,
    }
    if request.method == 'POST':
        form = ContactGroupeForm(request.POST, instance=contact)
        if form.is_valid():
            contact = form.save()
            after = {
                'nom_complet': contact.nom_complet,
                'email': contact.email,
                'fonction': contact.fonction,
            }
            _audit(request, 'UPDATE', 'contact_groupe', contact.pk, after, before)
            messages.success(request, f'Collaborateur {contact.nom_complet} mis à jour.')
            return redirect('group_list')
    else:
        form = ContactGroupeForm(instance=contact)
    return render(request, 'supervision/contact_form.html', {
        'form': form, 'group': group, 'contact': contact, 'mode': 'edit',
    })
