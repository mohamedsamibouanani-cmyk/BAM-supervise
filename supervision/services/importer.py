from io import BytesIO
from pathlib import Path
from decimal import Decimal, InvalidOperation
import pandas as pd
from django.conf import settings
from django.db import transaction

from supervision.models import (
    AttributDefinition, EnvoiSnapshot, FichierImport, ServiceReference,
    ServiceSnapshot, ValeurAttributSnapshot,
)
from .security import encrypt_file_bytes, encrypt_sensitive, sensitive_fingerprint
from .utils import clean_text, infer_attribute_scope, infer_attribute_type, normalize_value, sha256_bytes, stable_hash

STRUCTURAL_COLUMNS = {'CODE_ENVOI', 'NUM_COMMANDE', 'ARTICLE', 'DES_ARTICLE', 'ORG_COMMERCIALE', 'DATE_COMMANDE'}
REQUIRED_COLUMNS = {'CODE_ENVOI', 'ARTICLE', 'ORG_COMMERCIALE'}


class ImportValidationError(ValueError):
    pass


def _normalize_columns(df):
    df = df.copy()
    df.columns = [clean_text(c).upper().replace(' ', '_') for c in df.columns]
    return df


def _decimal_projection(normalized):
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


def import_excel(uploaded_file, systeme, superviseur):
    raw = uploaded_file.read()
    uploaded_file.seek(0)
    checksum = sha256_bytes(raw)
    if FichierImport.objects.filter(systeme=systeme, checksum_sha256=checksum).exists():
        raise ImportValidationError(f'Ce fichier {systeme.code_systeme} a déjà été importé.')

    # Parse only from memory. The original source is persisted encrypted at rest.
    try:
        df = _normalize_columns(pd.read_excel(BytesIO(raw), dtype=str))
    except Exception as exc:
        raise ImportValidationError(f'Classeur Excel illisible ou non supporté: {exc}') from exc

    target_dir = Path(settings.MEDIA_ROOT) / 'imports' / systeme.code_systeme
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(uploaded_file.name).name
    stored_path = target_dir / f'{checksum[:12]}_{safe_name}.enc'
    stored_path.write_bytes(encrypt_file_bytes(raw))

    fichier = FichierImport.objects.create(
        systeme=systeme,
        superviseur=superviseur,
        nom_fichier=safe_name,
        chemin_stockage=str(stored_path),
        checksum_sha256=checksum,
        statut_import=FichierImport.Statut.RECU,
    )
    try:
        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ImportValidationError(f'Colonnes obligatoires manquantes: {", ".join(sorted(missing))}')
        fichier.nb_lignes_source = len(df)
        df['ORG_COMMERCIALE'] = df['ORG_COMMERCIALE'].map(clean_text)
        df = df[df['ORG_COMMERCIALE'] == '2000'].copy()
        fichier.nb_lignes_retenues = len(df)
        if df.empty:
            raise ImportValidationError('Aucune ligne ORG_COMMERCIALE = 2000.')
        fichier.statut_import = FichierImport.Statut.VALIDE
        fichier.save(update_fields=['nb_lignes_source', 'nb_lignes_retenues', 'statut_import'])

        with transaction.atomic():
            _persist_dataframe(df, fichier)
            fichier.statut_import = FichierImport.Statut.CHARGE
            fichier.save(update_fields=['statut_import'])
        return fichier
    except Exception as exc:
        fichier.statut_import = FichierImport.Statut.ECHEC
        fichier.message_erreur = str(exc)
        fichier.save(update_fields=['statut_import', 'message_erreur', 'nb_lignes_source', 'nb_lignes_retenues'])
        raise


def _persist_dataframe(df, fichier):
    attr_columns = [c for c in df.columns if c not in STRUCTURAL_COLUMNS]
    definitions = {}
    for col in attr_columns:
        definitions[col], _ = AttributDefinition.objects.get_or_create(
            code_attribut=col,
            defaults={
                'libelle': col.replace('_', ' ').title(),
                'portee': infer_attribute_scope(col),
                'type_valeur': infer_attribute_type(col),
                'sensible': 'TELEPHONE' in col,
            },
        )

    df['_CODE_ENVOI'] = df['CODE_ENVOI'].map(clean_text)
    df = df[df['_CODE_ENVOI'] != '']
    for code_envoi, group in df.groupby('_CODE_ENVOI', sort=False):
        first = group.iloc[0]
        envoi = EnvoiSnapshot.objects.create(
            fichier_import=fichier,
            code_envoi=code_envoi,
            num_commande=clean_text(first.get('NUM_COMMANDE', '')),
            org_commerciale='2000',
            ligne_premiere=int(group.index[0]) + 2,
            empreinte_envoi=stable_hash(code_envoi, fichier.systeme.code_systeme, len(group)),
        )

        # Attributs de portée ENVOI: première valeur non vide observée.
        for col, definition in definitions.items():
            if definition.portee != AttributDefinition.Portee.ENVOI:
                continue
            raw_value = ''
            for v in group[col].tolist():
                if clean_text(v):
                    raw_value = v
                    break
            _create_value(envoi, None, definition, raw_value)

        occurrences = {}
        for idx, row in group.iterrows():
            code_service = clean_text(row.get('ARTICLE', ''))
            if not code_service:
                continue
            occurrences[code_service] = occurrences.get(code_service, 0) + 1
            service_ref = ServiceReference.objects.filter(code_service=code_service).first()
            service = ServiceSnapshot.objects.create(
                envoi_snapshot=envoi,
                service_ref=service_ref,
                code_service=code_service,
                libelle_service=clean_text(row.get('DES_ARTICLE', '')),
                numero_occurrence=occurrences[code_service],
                ligne_source=int(idx) + 2,
                empreinte_service=stable_hash(code_envoi, code_service, occurrences[code_service]),
            )
            for col, definition in definitions.items():
                if definition.portee == AttributDefinition.Portee.SERVICE:
                    _create_value(None, service, definition, row.get(col, ''))


def _create_value(envoi, service, definition, raw_value):
    raw_clean = clean_text(raw_value)
    normalized = normalize_value(raw_value, definition.type_valeur)
    stored_raw = raw_clean
    stored_comparable = normalized or None
    numeric_projection = _decimal_projection(normalized) if definition.type_valeur == 'NOMBRE' else None

    if definition.sensible and normalized:
        # Sensitive plaintext is encrypted at rest; equality uses a keyed HMAC fingerprint.
        stored_raw = encrypt_sensitive(raw_clean)
        stored_comparable = sensitive_fingerprint(normalized)
        numeric_projection = None

    ValeurAttributSnapshot.objects.create(
        envoi_snapshot=envoi,
        service_snapshot=service,
        attribut=definition,
        valeur_brute=stored_raw,
        valeur_normalisee=stored_comparable,
        valeur_numerique=numeric_projection,
        est_vide=(normalized == ''),
        format_source_conforme=None,
    )
