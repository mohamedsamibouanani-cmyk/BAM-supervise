from decimal import Decimal

from supervision.models import Anomalie, Motif, PredictionMotif, RegleMetier, Systeme

from .analysis import analyze_anomaly as _base_analyze_anomaly


def _attribute_prediction_is_relevant(prediction, anomaly):
    """Reject any diagnosis that is not about the attribute currently compared."""
    if anomaly.niveau != Anomalie.Niveau.ATTRIBUT or not anomaly.attribut_id:
        return True

    explanation = prediction.explication or {}
    analysed_field = str(explanation.get('attribut_analyse') or '').strip().upper()
    anomaly_field = anomaly.attribut.code_attribut.strip().upper()

    if analysed_field and analysed_field != anomaly_field:
        return False

    if prediction.regle_id and prediction.regle.attribut_id:
        if prediction.regle.attribut_id != anomaly.attribut_id:
            return False

    motif_level = str(prediction.motif.niveau_applicable or '').strip().upper()
    if motif_level and motif_level != Anomalie.Niveau.ATTRIBUT:
        return False

    # Pour un attribut ABSENT, une suggestion apprise/ML ne doit jamais remplacer
    # une preuve métier. On conserve uniquement les règles qui concernent réellement
    # cet attribut (dont les contrôles de format configurés pour le système cible).
    if anomaly.type_ecart == Anomalie.TypeEcart.ABSENT:
        if prediction.source_prediction != PredictionMotif.Source.REGLE:
            return False
        if explanation.get('role_diagnostic') == 'SUGGESTION':
            return False

    return True


def _attribute_sync_rule(motif):
    rule, _ = RegleMetier.objects.get_or_create(
        code_regle='DYNAMIC_ATTRIBUT_NON_SYNCHRONISE',
        defaults={
            'motif_suggere': motif,
            'niveau_anomalie': Anomalie.Niveau.ATTRIBUT,
            'type_controle': 'AUTRE',
            'expression_regle': {},
            'seuil_confiance': Decimal('1.0000'),
            'priorite': 9990,
            'actif': False,
        },
    )
    return rule


def _append_attribute_sync_constat(anomaly):
    """Use a neutral synchronization finding when no real cause is demonstrated."""
    motif = Motif.objects.filter(
        code_motif='ATTRIBUT_NON_SYNCHRONISE', actif=True
    ).first()
    if motif is None:
        return

    target = anomaly.systeme_ecart if anomaly.type_ecart == Anomalie.TypeEcart.ABSENT else None
    target_codes = [target.code_systeme] if target else []
    evidence = []
    if target:
        evidence.append({
            'systeme': target.code_systeme,
            'champ': anomaly.attribut.code_attribut if anomaly.attribut_id else 'ATTRIBUT',
            'constat': 'Attribut absent / non synchronisé dans ce système',
        })

    PredictionMotif.objects.create(
        anomalie=anomaly,
        source_prediction=PredictionMotif.Source.REGLE,
        regle=_attribute_sync_rule(motif),
        motif=motif,
        systeme_a_corriger_predit=target,
        rang=1,
        score_confiance=Decimal('1.0000'),
        explication={
            'diagnostic_dynamique': True,
            'attribut_analyse': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
            'indices': evidence,
            'message': 'Attribut détecté comme non synchronisé entre les systèmes.',
            'systemes_a_corriger': target_codes,
            'role_diagnostic': 'CONSTAT_ATTRIBUT',
        },
    )


def analyze_anomaly(anomaly):
    """Run the existing engine, then enforce strict N3 attribute isolation."""
    _base_analyze_anomaly(anomaly)

    if anomaly.niveau != Anomalie.Niveau.ATTRIBUT:
        return

    predictions = list(
        anomaly.predictions.select_related('motif', 'regle__attribut').order_by('rang')
    )
    invalid_ids = [
        prediction.pk
        for prediction in predictions
        if not _attribute_prediction_is_relevant(prediction, anomaly)
    ]
    if invalid_ids:
        anomaly.predictions.filter(pk__in=invalid_ids).delete()

    # Recompacte les rangs afin de garder un affichage stable après filtrage.
    for rank, prediction in enumerate(anomaly.predictions.order_by('rang', 'pk'), start=1):
        if prediction.rang != rank:
            prediction.rang = rank
            prediction.save(update_fields=['rang'])

    if not anomaly.predictions.exists():
        _append_attribute_sync_constat(anomaly)
