from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CampaignImportForm, ContactGroupeForm, ValidationMotifForm
from .models import (
    Anomalie, CampagneImport, CampagneSupervision, ExempleApprentissage,
    GroupeResponsable, HistoriqueAnomalie, JournalAudit, Systeme, ValidationMotif,
)
from .services.comparison import run_campaign
from .services.importer import import_excel
from .services.notifications import send_validation_email


def _audit(request, action, entity, entity_id, new_values=None):
    JournalAudit.objects.create(
        superviseur=request.user if request.user.is_authenticated else None,
        action=action,
        entite=entity,
        id_entite=entity_id,
        nouvelles_valeurs=new_values,
        adresse_ip=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
    )


@login_required
def dashboard(request):
    context = {
        'total': Anomalie.objects.count(),
        'ouvertes': Anomalie.objects.exclude(statut=Anomalie.Statut.RESOLUE).count(),
        'resolues': Anomalie.objects.filter(statut=Anomalie.Statut.RESOLUE).count(),
        'par_niveau': Anomalie.objects.values('niveau').annotate(total=Count('id')).order_by('niveau'),
        'campagnes': CampagneSupervision.objects.order_by('-demarree_le')[:5],
        'recentes': Anomalie.objects.select_related('systeme_ecart', 'attribut').order_by('-detectee_le')[:10],
    }
    return render(request, 'supervision/dashboard.html', context)


@login_required
def campaign_create(request):
    if request.method == 'POST':
        form = CampaignImportForm(request.POST, request.FILES)
        if form.is_valid():
            systems = {s.code_systeme: s for s in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])}
            if len(systems) != 3:
                messages.error(request, 'Initialisez les systèmes SMI, SICOM et SIBO avec seed_bam.')
                return redirect('campaign_create')
            campaign = CampagneSupervision.objects.create(superviseur=request.user)
            try:
                uploaded = {
                    'SMI': form.cleaned_data['fichier_smi'],
                    'SICOM': form.cleaned_data['fichier_sicom'],
                    'SIBO': form.cleaned_data['fichier_sibo'],
                }
                for code, file_obj in uploaded.items():
                    fimport = import_excel(file_obj, systems[code], request.user)
                    CampagneImport.objects.create(campagne=campaign, systeme=systems[code], fichier_import=fimport)
                run_campaign(campaign)
                _audit(request, 'CREATE', 'campagne_supervision', campaign.pk, {'nb_anomalies': campaign.nb_anomalies})
                messages.success(request, f'Campagne terminée : {campaign.nb_anomalies} anomalie(s) détectée(s).')
                return redirect('anomaly_list')
            except Exception as exc:
                messages.error(request, f'Échec de la campagne : {exc}')
    else:
        form = CampaignImportForm()
    return render(request, 'supervision/campaign_form.html', {'form': form})


@login_required
def anomaly_list(request):
    qs = Anomalie.objects.select_related('systeme_ecart', 'attribut', 'campagne').order_by('-detectee_le')
    for field in ('niveau', 'type_ecart', 'statut'):
        value = request.GET.get(field)
        if value:
            qs = qs.filter(**{field: value})
    system = request.GET.get('systeme')
    if system:
        qs = qs.filter(systeme_ecart__code_systeme=system)
    return render(request, 'supervision/anomaly_list.html', {'anomalies': qs[:500], 'systems': Systeme.objects.all()})


@login_required
def anomaly_detail(request, pk):
    anomaly = get_object_or_404(
        Anomalie.objects.select_related('systeme_ecart', 'attribut', 'campagne').prefetch_related('details__systeme', 'predictions__motif', 'validations__motif_final'),
        pk=pk,
    )
    return render(request, 'supervision/anomaly_detail.html', {'anomaly': anomaly})


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
            validation = ValidationMotif.objects.create(
                anomalie=anomaly,
                prediction_retenue=prediction,
                motif_final=form.cleaned_data['motif_final'],
                systeme_a_corriger_final=form.cleaned_data['systeme_a_corriger_final'],
                superviseur=request.user,
                decision=form.cleaned_data['decision'],
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
            _audit(request, 'VALIDATE', 'validation_motif', validation.pk, {'motif': validation.motif_final.code_motif})
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
def group_list(request):
    groups = GroupeResponsable.objects.select_related('systeme').prefetch_related('contacts').order_by('systeme__ordre_comparaison', 'nom_groupe')
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
            _audit(request, 'CREATE', 'contact_groupe', contact.pk, {'email': contact.email, 'systeme': group.systeme.code_systeme})
            messages.success(request, f'Adresse {contact.email} ajoutée au groupe {group.nom_groupe}.')
            return redirect('group_list')
    else:
        form = ContactGroupeForm()
    return render(request, 'supervision/contact_form.html', {'form': form, 'group': group})
