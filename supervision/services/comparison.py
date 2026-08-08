from collections import Counter
from django.db import transaction
from django.utils import timezone

from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    DetailComparaison, EnvoiSnapshot, HistoriqueAnomalie, ServiceSnapshot,
    Systeme, ValeurAttributSnapshot, VerificationResolution,
)
from .analysis import analyze_anomaly
from .security import masked_sensitive_value
from .utils import stable_hash


class ComparisonError(ValueError):
    pass


def run_campaign(campaign):
    links = list(campaign.imports.select_related('systeme', 'fichier_import'))
    if len(links) != 3 or {x.systeme.code_systeme for x in links} != {'SMI', 'SICOM', 'SIBO'}:
        raise ComparisonError('Une campagne doit contenir exactement un fichier SMI, SICOM et SIBO.')

    campaign.statut = CampagneSupervision.Statut.EN_COURS
    campaign.save(update_fields=['statut'])
    try:
        with transaction.atomic():
            campaign.anomalies.all().delete()
            systems = {link.systeme.code_systeme: link.systeme for link in links}
            imports = {link.systeme.code_systeme: link.fichier_import for link in links}
            env_maps = {
                code: {e.code_envoi: e for e in EnvoiSnapshot.objects.filter(fichier_import=imp).prefetch_related('services', 'valeurs_attribut')}
                for code, imp in imports.items()
            }
            _compare_envois(campaign, systems, env_maps)
            campaign.nb_anomalies = campaign.anomalies.count()
            campaign.statut = CampagneSupervision.Statut.TERMINEE
            campaign.terminee_le = timezone.now()
            campaign.save(update_fields=['nb_anomalies', 'statut', 'terminee_le'])
            _recontrol_previous(campaign)
            for anomaly in campaign.anomalies.all():
                analyze_anomaly(anomaly)
        return campaign
    except Exception as exc:
        campaign.statut = CampagneSupervision.Statut.ECHEC
        campaign.message_erreur = str(exc)
        campaign.terminee_le = timezone.now()
        campaign.save(update_fields=['statut', 'message_erreur', 'terminee_le'])
        raise


def _create_anomaly(campaign, level, gap_type, code_envoi, systems, details, code_service='', attribute=None, gap_system=None):
    fingerprint = stable_hash(level, gap_type, code_envoi, code_service, attribute.code_attribut if attribute else '', gap_system.code_systeme if gap_system else '')
    anomaly, _ = Anomalie.objects.get_or_create(
        campagne=campaign,
        empreinte_anomalie=fingerprint,
        defaults={
            'niveau': level, 'type_ecart': gap_type, 'code_envoi': code_envoi,
            'code_service': code_service, 'attribut': attribute, 'systeme_ecart': gap_system,
            'statut': Anomalie.Statut.DETECTEE,
        }
    )
    for sys_code, payload in details.items():
        DetailComparaison.objects.update_or_create(
            anomalie=anomaly, systeme=systems[sys_code],
            defaults=payload,
        )
    HistoriqueAnomalie.objects.get_or_create(
        anomalie=anomaly, nouveau_statut=Anomalie.Statut.DETECTEE,
        defaults={'source_evenement': 'MOTEUR', 'commentaire': 'Écart détecté par le moteur de comparaison.'}
    )
    return anomaly


def _compare_envois(campaign, systems, env_maps):
    codes = set().union(*(set(m) for m in env_maps.values()))
    for code_envoi in sorted(codes):
        present = {s: code_envoi in env_maps[s] for s in systems}
        missing = [s for s, ok in present.items() if not ok]
        if missing:
            for miss in missing:
                details = {}
                for s in systems:
                    e = env_maps[s].get(code_envoi)
                    details[s] = {'objet_present': bool(e), 'est_ecart': s == miss, 'ligne_source': e.ligne_premiere if e else None}
                _create_anomaly(campaign, 'ENVOI', 'ABSENT', code_envoi, systems, details, gap_system=systems[miss])
            continue
        envois = {s: env_maps[s][code_envoi] for s in systems}
        _compare_services(campaign, systems, code_envoi, envois)
        _compare_envoi_attributes(campaign, systems, code_envoi, envois)


def _service_map(envoi):
    result = {}
    for s in envoi.services.all():
        result.setdefault(s.code_service, s)
    return result


def _compare_services(campaign, systems, code_envoi, envois):
    maps = {s: _service_map(e) for s, e in envois.items()}
    service_codes = set().union(*(set(m) for m in maps.values()))
    for service_code in sorted(service_codes):
        missing = [s for s in systems if service_code not in maps[s]]
        if missing:
            for miss in missing:
                details = {}
                for s in systems:
                    srv = maps[s].get(service_code)
                    details[s] = {'objet_present': bool(srv), 'est_ecart': s == miss, 'ligne_source': srv.ligne_source if srv else None}
                _create_anomaly(campaign, 'SERVICE', 'ABSENT', code_envoi, systems, details, code_service=service_code, gap_system=systems[miss])
            continue
        services = {s: maps[s][service_code] for s in systems}
        _compare_service_attributes(campaign, systems, code_envoi, service_code, services)


def _values_by_attribute(queryset):
    return {v.attribut_id: v for v in queryset.select_related('attribut')}


def _compare_envoi_attributes(campaign, systems, code_envoi, envois):
    values = {s: _values_by_attribute(e.valeurs_attribut.all()) for s, e in envois.items()}
    _compare_attribute_maps(campaign, systems, code_envoi, '', values)


def _compare_service_attributes(campaign, systems, code_envoi, service_code, services):
    values = {s: _values_by_attribute(srv.valeurs_attribut.all()) for s, srv in services.items()}
    _compare_attribute_maps(campaign, systems, code_envoi, service_code, values)


def _compare_attribute_maps(campaign, systems, code_envoi, service_code, values):
    attr_ids = set().union(*(set(m) for m in values.values()))
    for attr_id in attr_ids:
        attr = AttributDefinition.objects.get(pk=attr_id)
        vals = {s: values[s].get(attr_id) for s in systems}
        nonempty_systems = [s for s, v in vals.items() if v and not v.est_vide]
        missing_systems = [s for s in systems if not vals[s] or vals[s].est_vide]
        if nonempty_systems and missing_systems:
            for miss in missing_systems:
                details = _attribute_details(systems, vals, miss)
                _create_anomaly(campaign, 'ATTRIBUT', 'ABSENT', code_envoi, systems, details, code_service=service_code, attribute=attr, gap_system=systems[miss])
            continue
        normalized = {s: (vals[s].valeur_normalisee or '') for s in systems if vals[s]}
        unique = set(normalized.values())
        if len(unique) > 1:
            counts = Counter(normalized.values())
            majority = counts.most_common(1)[0][0] if counts else None
            divergent = [s for s, v in normalized.items() if v != majority] if list(counts.values()).count(max(counts.values())) == 1 else []
            gap_system = systems[divergent[0]] if len(divergent) == 1 else None
            details = _attribute_details(systems, vals, divergent[0] if len(divergent) == 1 else None)
            _create_anomaly(campaign, 'ATTRIBUT', 'DIFFERENT', code_envoi, systems, details, code_service=service_code, attribute=attr, gap_system=gap_system)


def _attribute_details(systems, vals, gap_code=None):
    details = {}
    for s in systems:
        v = vals[s]
        sensitive = bool(v and v.attribut.sensible)
        details[s] = {
            'objet_present': bool(v and not v.est_vide),
            'valeur_brute': masked_sensitive_value(v.valeur_brute) if sensitive else (v.valeur_brute if v else None),
            'valeur_normalisee': masked_sensitive_value(v.valeur_normalisee) if sensitive else (v.valeur_normalisee if v else None),
            'format_source_conforme': v.format_source_conforme if v else None,
            'est_ecart': s == gap_code,
        }
    return details


def _recontrol_previous(campaign):
    current_fingerprints = set(campaign.anomalies.values_list('empreinte_anomalie', flat=True))
    previous = Anomalie.objects.exclude(campagne=campaign).exclude(statut=Anomalie.Statut.RESOLUE)
    for old in previous:
        result = VerificationResolution.Resultat.PERSISTANTE if old.empreinte_anomalie in current_fingerprints else VerificationResolution.Resultat.RESOLUE
        VerificationResolution.objects.get_or_create(
            anomalie=old, campagne_controle=campaign,
            defaults={'resultat': result, 'commentaire': 'Vérification automatique lors du nouvel import.'}
        )
        new_status = Anomalie.Statut.PERSISTANTE if result == VerificationResolution.Resultat.PERSISTANTE else Anomalie.Statut.RESOLUE
        if old.statut != new_status:
            previous_status = old.statut
            old.statut = new_status
            if new_status == Anomalie.Statut.RESOLUE:
                old.cloturee_le = timezone.now()
            old.save(update_fields=['statut', 'cloturee_le'])
            HistoriqueAnomalie.objects.create(
                anomalie=old, ancien_statut=previous_status, nouveau_statut=new_status,
                source_evenement='CONTROLE', commentaire='Statut mis à jour après nouvelle comparaison.'
            )
