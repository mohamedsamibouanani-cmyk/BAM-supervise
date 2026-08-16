from django.db import transaction
from django.utils import timezone

from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    DetailComparaison, EnvoiSnapshot, HistoriqueAnomalie, ServiceSnapshot,
    Systeme, ValeurAttributSnapshot, VerificationResolution,
)
from .analysis_runtime import analyze_anomaly
from .security import masked_sensitive_value
from .utils import stable_hash


# Le niveau N3 BAM porte uniquement sur trois concepts métier dans le moteur
# de comparaison courant. Les alias historiques restent acceptés.
N3_BUSINESS_ATTRIBUTE_CODES = {
    'MONTANT_CRBT', 'CRBT',
    'TELEPHONE_NOTIFICATION', 'TELEPHONE',
    'MONTANT_VALEUR_DECLAREE', 'VALEUR_DECLAREE',
}


def is_n3_business_attribute(attribute):
    return bool(
        attribute
        and str(attribute.code_attribut or '').strip().upper()
        in N3_BUSINESS_ATTRIBUTE_CODES
    )


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
                code: {
                    e.code_envoi: e
                    for e in EnvoiSnapshot.objects.filter(fichier_import=imp).prefetch_related(
                        'services__valeurs_attribut__attribut', 'valeurs_attribut__attribut'
                    )
                }
                for code, imp in imports.items()
            }
            _compare_envois(campaign, systems, env_maps)
            campaign.nb_anomalies = campaign.anomalies.count()
            campaign.statut = CampagneSupervision.Statut.TERMINEE
            campaign.terminee_le = timezone.now()
            campaign.save(update_fields=['nb_anomalies', 'statut', 'terminee_le'])
            _recontrol_previous(campaign, env_maps)
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
    fingerprint = stable_hash(
        level, gap_type, code_envoi, code_service,
        attribute.code_attribut if attribute else '',
        gap_system.code_systeme if gap_system else '',
    )
    anomaly, _ = Anomalie.objects.get_or_create(
        campagne=campaign,
        empreinte_anomalie=fingerprint,
        defaults={
            'niveau': level,
            'type_ecart': gap_type,
            'code_envoi': code_envoi,
            'code_service': code_service,
            'attribut': attribute,
            'systeme_ecart': gap_system,
            'statut': Anomalie.Statut.DETECTEE,
        },
    )
    for sys_code, payload in details.items():
        DetailComparaison.objects.update_or_create(
            anomalie=anomaly,
            systeme=systems[sys_code],
            defaults=payload,
        )
    HistoriqueAnomalie.objects.get_or_create(
        anomalie=anomaly,
        nouveau_statut=Anomalie.Statut.DETECTEE,
        defaults={
            'source_evenement': 'MOTEUR',
            'commentaire': 'Écart détecté par le moteur de comparaison.',
        },
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
                    details[s] = {
                        'objet_present': bool(e),
                        'est_ecart': s == miss,
                        'ligne_source': e.ligne_premiere if e else None,
                    }
                _create_anomaly(
                    campaign, 'ENVOI', 'ABSENT', code_envoi, systems, details,
                    gap_system=systems[miss],
                )
            continue
        envois = {s: env_maps[s][code_envoi] for s in systems}
        _compare_services(campaign, systems, code_envoi, envois)
        _compare_envoi_attributes(campaign, systems, code_envoi, envois)


def _service_map(envoi):
    result = {}
    for service in envoi.services.all():
        result.setdefault(service.code_service, service)
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
                    details[s] = {
                        'objet_present': bool(srv),
                        'est_ecart': s == miss,
                        'ligne_source': srv.ligne_source if srv else None,
                    }
                _create_anomaly(
                    campaign, 'SERVICE', 'ABSENT', code_envoi, systems, details,
                    code_service=service_code, gap_system=systems[miss],
                )
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
        if not is_n3_business_attribute(attr):
            continue
        vals = {s: values[s].get(attr_id) for s in systems}
        nonempty_systems = [s for s, v in vals.items() if v and not v.est_vide]
        missing_systems = [s for s in systems if not vals[s] or vals[s].est_vide]
        if nonempty_systems and missing_systems:
            for miss in missing_systems:
                details = _attribute_details(systems, vals, miss)
                _create_anomaly(
                    campaign, 'ATTRIBUT', 'ABSENT', code_envoi, systems, details,
                    code_service=service_code, attribute=attr, gap_system=systems[miss],
                )
            continue
        if len(nonempty_systems) != len(systems):
            continue

        normalized = {s: _comparable_attribute_value(vals[s]) for s in systems}
        if len(set(normalized.values())) <= 1:
            continue

        # Une différence de valeur est un constat neutre. Même si deux systèmes
        # portent la même valeur et le troisième une autre, cela ne démontre pas
        # laquelle est la valeur métier correcte.
        details = _attribute_details(
            systems,
            vals,
            gap_code=None,
            mark_all_as_gap=True,
        )
        _create_anomaly(
            campaign,
            'ATTRIBUT',
            'DIFFERENT',
            code_envoi,
            systems,
            details,
            code_service=service_code,
            attribute=attr,
            gap_system=None,
        )


def _comparable_attribute_value(value):
    """Return the canonical value used for cross-system equality checks."""
    if value.valeur_normalisee is not None:
        return str(value.valeur_normalisee).strip()
    return str(value.valeur_brute or '').strip()


def _attribute_details(systems, vals, gap_code=None, mark_all_as_gap=False):
    details = {}
    for s in systems:
        v = vals[s]
        sensitive = bool(v and v.attribut.sensible)
        details[s] = {
            'objet_present': bool(v and not v.est_vide),
            'valeur_brute': masked_sensitive_value(v.valeur_brute) if sensitive else (v.valeur_brute if v else None),
            'valeur_normalisee': masked_sensitive_value(v.valeur_normalisee) if sensitive else (v.valeur_normalisee if v else None),
            'format_source_conforme': v.format_source_conforme if v else None,
            'est_ecart': mark_all_as_gap or s == gap_code,
        }
    return details


def _current_service_maps(code_envoi, env_maps):
    """Return current service maps only when the shipment is observable in all systems."""
    if any(code_envoi not in env_maps[code] for code in env_maps):
        return None
    return {
        code: _service_map(env_maps[code][code_envoi])
        for code in env_maps
    }


def _current_attribute_values(old, env_maps):
    """Read the old business attribute from the new campaign without relying on fingerprints.

    This deliberately evaluates the raw current snapshots. A change of engine rules or
    fingerprint generation must never be interpreted as a business correction.
    """
    if not old.attribut_id:
        return None
    if any(old.code_envoi not in env_maps[code] for code in env_maps):
        return None

    values = {}
    if old.code_service:
        service_maps = _current_service_maps(old.code_envoi, env_maps)
        if service_maps is None:
            return None
        if any(old.code_service not in service_maps[code] for code in service_maps):
            return None
        for code, services in service_maps.items():
            values[code] = services[old.code_service].valeurs_attribut.filter(
                attribut_id=old.attribut_id
            ).first()
    else:
        for code in env_maps:
            values[code] = env_maps[code][old.code_envoi].valeurs_attribut.filter(
                attribut_id=old.attribut_id
            ).first()
    return values


def _evaluate_previous_anomaly(old, env_maps):
    """Evaluate one historical anomaly against the new campaign.

    RESOLUE is returned only when the new files actually contain enough data to prove
    the correction. Missing scope/data yields INDETERMINABLE instead of a false RESOLUE.
    """
    codes = tuple(env_maps.keys())
    present = {code: old.code_envoi in env_maps[code] for code in codes}

    # If the shipment is outside the new campaign, the new import cannot prove anything
    # about the previous anomaly.
    if not any(present.values()):
        return VerificationResolution.Resultat.INDETERMINABLE

    if old.niveau == Anomalie.Niveau.ENVOI:
        if not old.systeme_ecart_id:
            return VerificationResolution.Resultat.INDETERMINABLE
        target_code = old.systeme_ecart.code_systeme
        return (
            VerificationResolution.Resultat.PERSISTANTE
            if not present.get(target_code, False)
            else VerificationResolution.Resultat.RESOLUE
        )

    # N2/N3 cannot be rechecked while a parent shipment is missing in the current files.
    if not all(present.values()):
        return VerificationResolution.Resultat.INDETERMINABLE

    service_maps = _current_service_maps(old.code_envoi, env_maps)
    if old.niveau == Anomalie.Niveau.SERVICE:
        if not old.systeme_ecart_id or service_maps is None:
            return VerificationResolution.Resultat.INDETERMINABLE
        target_code = old.systeme_ecart.code_systeme
        return (
            VerificationResolution.Resultat.PERSISTANTE
            if old.code_service not in service_maps[target_code]
            else VerificationResolution.Resultat.RESOLUE
        )

    if old.niveau != Anomalie.Niveau.ATTRIBUT:
        return VerificationResolution.Resultat.INDETERMINABLE

    values = _current_attribute_values(old, env_maps)
    if values is None:
        return VerificationResolution.Resultat.INDETERMINABLE

    if old.type_ecart == Anomalie.TypeEcart.ABSENT:
        if not old.systeme_ecart_id:
            return VerificationResolution.Resultat.INDETERMINABLE
        target_code = old.systeme_ecart.code_systeme
        target_value = values.get(target_code)
        # If the attribute disappeared from every current source, the current campaign
        # is not comparable enough to prove either persistence or resolution.
        if not any(value is not None and not value.est_vide for value in values.values()):
            return VerificationResolution.Resultat.INDETERMINABLE
        return (
            VerificationResolution.Resultat.PERSISTANTE
            if target_value is None or target_value.est_vide
            else VerificationResolution.Resultat.RESOLUE
        )

    if old.type_ecart == Anomalie.TypeEcart.DIFFERENT:
        if any(value is None or value.est_vide for value in values.values()):
            return VerificationResolution.Resultat.INDETERMINABLE
        comparable = {_comparable_attribute_value(value) for value in values.values()}
        return (
            VerificationResolution.Resultat.PERSISTANTE
            if len(comparable) > 1
            else VerificationResolution.Resultat.RESOLUE
        )

    return VerificationResolution.Resultat.INDETERMINABLE


def _recontrol_previous(campaign, env_maps):
    previous = (
        Anomalie.objects.exclude(campagne=campaign)
        .exclude(statut=Anomalie.Statut.RESOLUE)
        .select_related('systeme_ecart', 'attribut')
    )
    for old in previous:
        result = _evaluate_previous_anomaly(old, env_maps)
        VerificationResolution.objects.update_or_create(
            anomalie=old,
            campagne_controle=campaign,
            defaults={
                'resultat': result,
                'commentaire': (
                    'Vérification automatique sur les données comparables du nouvel import.'
                    if result != VerificationResolution.Resultat.INDETERMINABLE
                    else 'Vérification indéterminable : le nouvel import ne couvre pas suffisamment ce dossier.'
                ),
            },
        )

        # INDETERMINABLE is evidence that the new campaign cannot conclude. It must
        # never close an old dossier nor erase its current workflow state.
        if result == VerificationResolution.Resultat.INDETERMINABLE:
            continue

        new_status = (
            Anomalie.Statut.PERSISTANTE
            if result == VerificationResolution.Resultat.PERSISTANTE
            else Anomalie.Statut.RESOLUE
        )
        if old.statut == new_status:
            continue

        previous_status = old.statut
        old.statut = new_status
        old.cloturee_le = timezone.now() if new_status == Anomalie.Statut.RESOLUE else None
        old.save(update_fields=['statut', 'cloturee_le'])
        HistoriqueAnomalie.objects.create(
            anomalie=old,
            ancien_statut=previous_status,
            nouveau_statut=new_status,
            source_evenement='CONTROLE',
            commentaire='Statut mis à jour après recontrôle sur des données comparables.',
        )
