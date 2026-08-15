import hashlib
from collections import Counter
from pathlib import Path

import joblib
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from supervision.models import ExempleApprentissage, ModeleML


MIN_TRAINING_EXAMPLES = 6
MIN_CLASSES = 2


def _training_features(example):
    """Build the same feature family consumed by the ML prediction service."""
    anomaly = example.validation.anomalie
    return {
        'niveau': anomaly.niveau,
        'type_ecart': anomaly.type_ecart,
        'code_service': anomaly.code_service or '',
        'attribut': anomaly.attribut.code_attribut if anomaly.attribut_id else '',
        'systeme_ecart': anomaly.systeme_ecart.code_systeme if anomaly.systeme_ecart_id else '',
        'nb_valeurs_vides': sum(1 for detail in anomaly.details.all() if not detail.objet_present),
        'nb_formats_invalides': sum(
            1 for detail in anomaly.details.all() if detail.format_source_conforme is False
        ),
    }


def _eligible_examples():
    return list(
        ExempleApprentissage.objects.filter(eligible=True)
        .select_related(
            'motif_label',
            'validation__anomalie__attribut',
            'validation__anomalie__systeme_ecart',
        )
        .prefetch_related('validation__anomalie__details')
        .order_by('cree_le')
    )


def _campaign_is_fully_validated(campaign):
    anomalies = campaign.anomalies.all()
    if not anomalies.exists():
        return False
    for anomaly in anomalies:
        if not anomaly.validations.filter(est_finale=True).exists():
            return False
    return True


def _campaign_already_trained(campaign):
    for model in ModeleML.objects.order_by('-entraine_le')[:50]:
        metrics = model.metriques or {}
        if str(metrics.get('campagne_declencheuse_id', '')) == str(campaign.pk):
            return True
    return False


def retrain_after_campaign_if_ready(campaign, *, min_examples=MIN_TRAINING_EXAMPLES):
    """
    Retrain one ML model after a campaign has been completely reviewed.

    Human validations remain the source of labels. The model predicts motifs only;
    it never closes an anomaly or replaces the supervisor's final decision.
    """
    campaign.refresh_from_db()
    if not _campaign_is_fully_validated(campaign):
        return {'trained': False, 'reason': 'campagne_non_entierement_validee'}
    if _campaign_already_trained(campaign):
        return {'trained': False, 'reason': 'campagne_deja_apprise'}

    examples = _eligible_examples()
    if len(examples) < min_examples:
        return {
            'trained': False,
            'reason': 'exemples_insuffisants',
            'nb_exemples': len(examples),
            'minimum': min_examples,
        }

    labels = [example.motif_label.code_motif for example in examples]
    class_counts = Counter(labels)
    if len(class_counts) < MIN_CLASSES:
        return {
            'trained': False,
            'reason': 'classes_insuffisantes',
            'nb_classes': len(class_counts),
        }

    features = [_training_features(example) for example in examples]
    pipeline = Pipeline([
        ('vectorizer', DictVectorizer(sparse=True)),
        ('classifier', LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42)),
    ])
    pipeline.fit(features, labels)
    training_accuracy = float(pipeline.score(features, labels))

    model_dir = Path(settings.MEDIA_ROOT) / 'ml_models'
    model_dir.mkdir(parents=True, exist_ok=True)
    now = timezone.now()
    version = f"motif-{now.strftime('%Y%m%d%H%M%S')}-c{campaign.pk}"
    model_path = model_dir / f'{version}.joblib'
    joblib.dump(pipeline, model_path)
    checksum = hashlib.sha256(model_path.read_bytes()).hexdigest()

    with transaction.atomic():
        ModeleML.objects.filter(actif=True).update(actif=False)
        model = ModeleML.objects.create(
            nom_modele='Classification des motifs BAM Supervise',
            algorithme='LogisticRegression + DictVectorizer',
            version_modele=version,
            entraine_le=now,
            nb_exemples=len(examples),
            metriques={
                'accuracy_apprentissage': round(training_accuracy, 4),
                'nb_classes': len(class_counts),
                'classes': dict(class_counts),
                'campagne_declencheuse_id': campaign.pk,
                'note': "Métrique calculée sur l'ensemble d'apprentissage du prototype.",
            },
            chemin_fichier=str(model_path),
            checksum_sha256=checksum,
            actif=True,
        )
        dataset_version = version
        ExempleApprentissage.objects.filter(pk__in=[example.pk for example in examples]).update(
            version_dataset=dataset_version
        )

    return {
        'trained': True,
        'model_id': model.pk,
        'version': version,
        'nb_exemples': len(examples),
        'nb_classes': len(class_counts),
        'accuracy_apprentissage': round(training_accuracy, 4),
    }
