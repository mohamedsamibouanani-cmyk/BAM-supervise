from __future__ import annotations

from django.utils.text import slugify

from supervision.models import Anomalie, ExempleApprentissage, Motif, ValidationMotif

from .ml_policy import NON_CAUSAL_MOTIF_CODES


def available_learning_causes(anomaly):
    """Causes que le superviseur peut confirmer pour alimenter le ML.

    On exclut volontairement les constats techniques déjà connus du moteur de
    comparaison. Une cause proposée ici doit être une explication métier réelle,
    validée humainement.
    """
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return Motif.objects.none()
    return (
        Motif.objects.filter(
            actif=True,
            niveau_applicable=anomaly.niveau,
        )
        .exclude(code_motif__in=NON_CAUSAL_MOTIF_CODES)
        .order_by('libelle')
    )


def resolve_learning_cause(anomaly, cause_id=None, new_label=''):
    """Résout une cause confirmée sans jamais transformer un constat en cause ML."""
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return None

    if cause_id:
        cause = available_learning_causes(anomaly).filter(pk=cause_id).first()
        if cause is not None:
            return cause

    label = str(new_label or '').strip()
    # Pour ATTRIBUT/DIFFERENT, l'interface garde volontairement le choix simple
    # des systèmes à corriger : pas de saisie libre de nouvelle cause ici.
    if not label or (
        anomaly.niveau == Anomalie.Niveau.ATTRIBUT
        and anomaly.type_ecart == Anomalie.TypeEcart.DIFFERENT
    ):
        return None

    existing = Motif.objects.filter(
        libelle__iexact=label,
        niveau_applicable=anomaly.niveau,
    ).first()
    if existing is not None:
        if existing.code_motif in NON_CAUSAL_MOTIF_CODES:
            return None
        if not existing.actif:
            existing.actif = True
            existing.save(update_fields=['actif'])
        return existing

    base = slugify(label).replace('-', '_').upper()[:60] or 'CAUSE_APPRISE'
    code = base
    suffix = 2
    while Motif.objects.filter(code_motif=code).exists():
        code = f'{base[:70]}_{suffix}'
        suffix += 1

    return Motif.objects.create(
        code_motif=code,
        libelle=label,
        description=(
            'Cause réelle ajoutée par le superviseur pour enrichir '
            "l'apprentissage de BAM Supervise."
        ),
        niveau_applicable=anomaly.niveau,
        categorie='APPRENTISSAGE',
        actif=True,
    )


def apply_learning_cause(validation, cause):
    """Associe UNE cause causale à UNE décision de dossier.

    ExempleApprentissage est OneToOne avec ValidationMotif. On remplace donc le
    label technique éventuellement créé lors de la validation, sans dupliquer un
    même dossier simplement parce que plusieurs systèmes sont destinataires.
    """
    if cause is None:
        return None
    anomaly = validation.anomalie
    if anomaly.niveau == Anomalie.Niveau.SERVICE:
        return None
    if cause.code_motif in NON_CAUSAL_MOTIF_CODES:
        return None

    features = {
        'niveau': anomaly.niveau,
        'type_ecart': anomaly.type_ecart,
        'service': anomaly.code_service,
        'code_service': anomaly.code_service,
        'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
        'systeme_ecart': anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
        'nb_valeurs_vides': sum(1 for d in anomaly.details.all() if not d.objet_present),
        'nb_formats_invalides': sum(
            1 for d in anomaly.details.all() if d.format_source_conforme is False
        ),
    }

    example, _ = ExempleApprentissage.objects.update_or_create(
        validation=validation,
        defaults={
            'motif_label': cause,
            'systeme_a_corriger_label': validation.systeme_a_corriger_final,
            'caracteristiques': features,
            'eligible': validation.decision != ValidationMotif.Decision.INCONNU,
            'raison_exclusion': (
                'Décision à investiguer'
                if validation.decision == ValidationMotif.Decision.INCONNU
                else ''
            ),
        },
    )
    return example
