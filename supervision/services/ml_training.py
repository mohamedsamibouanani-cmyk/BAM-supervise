from __future__ import annotations

import hashlib
import logging
from collections import Counter
from pathlib import Path

import joblib
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from supervision.models import ExempleApprentissage, ModeleML


logger = logging.getLogger(__name__)

# Pour la démonstration BAM Supervise, le premier modèle peut être entraîné dès
# 6 décisions humaines éligibles, à condition d'avoir au moins deux motifs
# différents représentés par au moins deux exemples chacun.
MIN_TRAINING_EXAMPLES = 6
MIN_EXAMPLES_PER_CLASS = 2
RETRAIN_INCREMENT = 5


def _normalise_features(example):
    source = dict(example.caracteristiques or {})
    return {
        'niveau': source.get('niveau', ''),
        'type_ecart': source.get('type_ecart', ''),
        'code_service': source.get('code_service', source.get('service', '')) or '',
        'attribut': source.get('attribut', '') or '',
        'systeme_ecart': source.get('systeme_ecart', '') or '',
        'nb_valeurs_vides': int(source.get('nb_valeurs_vides', 0) or 0),
        'nb_formats_invalides': int(source.get('nb_formats_invalides', 0) or 0),
    }


def training_status():
    examples = list(
        ExempleApprentissage.objects.filter(eligible=True)
        .select_related('motif_label')
        .order_by('pk')
    )
    counts = Counter(example.motif_label.code_motif for example in examples)
    trainable_classes = [code for code, count in counts.items() if count >= MIN_EXAMPLES_PER_CLASS]
    trainable_examples = sum(counts[code] for code in trainable_classes)
    active_model = ModeleML.objects.filter(actif=True).order_by('-entraine_le').first()
    ready = trainable_examples >= MIN_TRAINING_EXAMPLES and len(trainable_classes) >= 2
    return {
        'eligible_examples': len(examples),
        'trainable_examples': trainable_examples,
        'motif_classes': len(counts),
        'trainable_classes': len(trainable_classes),
        'minimum_examples': MIN_TRAINING_EXAMPLES,
        'ready': ready,
        'active_model': active_model,
    }


def _should_retrain(status):
    if not status['ready']:
        return False
    model = status['active_model']
    if model is None:
        return True
    return status['trainable_examples'] >= model.nb_exemples + RETRAIN_INCREMENT


def maybe_retrain_model():
    """Réentraîne le modèle si l'historique validé est suffisant.

    Cette fonction est volontairement sans effet si le seuil métier n'est pas
    atteint. Une erreur d'entraînement est journalisée et ne doit jamais annuler
    une décision déjà validée par le superviseur.
    """
    status = training_status()
    if not _should_retrain(status):
        return None

    examples = list(
        ExempleApprentissage.objects.filter(eligible=True)
        .select_related('motif_label')
        .order_by('pk')
    )
    class_counts = Counter(example.motif_label.code_motif for example in examples)
    allowed = {
        code for code, count in class_counts.items()
        if count >= MIN_EXAMPLES_PER_CLASS
    }
    examples = [example for example in examples if example.motif_label.code_motif in allowed]
    if len(examples) < MIN_TRAINING_EXAMPLES or len(allowed) < 2:
        return None

    x = [_normalise_features(example) for example in examples]
    y = [example.motif_label.code_motif for example in examples]

    pipeline = Pipeline([
        ('vectorizer', DictVectorizer(sparse=True)),
        ('classifier', LogisticRegression(max_iter=1000, class_weight='balanced')),
    ])

    metrics = {}
    test_size = max(len(allowed), round(len(examples) * 0.25))
    can_holdout = test_size < len(examples) and all(class_counts[code] >= 2 for code in allowed)
    if can_holdout:
        try:
            x_train, x_test, y_train, y_test = train_test_split(
                x,
                y,
                test_size=test_size,
                random_state=42,
                stratify=y,
            )
            pipeline.fit(x_train, y_train)
            metrics['accuracy_validation'] = round(float(accuracy_score(y_test, pipeline.predict(x_test))), 4)
            metrics['nb_validation'] = len(y_test)
        except ValueError:
            pass

    # Le modèle actif final est toujours ajusté sur la totalité des décisions
    # humaines éligibles, après l'évaluation éventuelle ci-dessus.
    pipeline.fit(x, y)
    metrics['nb_classes'] = len(set(y))
    metrics['classes'] = sorted(set(y))

    model_dir = Path(settings.MEDIA_ROOT) / 'ml_models'
    model_dir.mkdir(parents=True, exist_ok=True)
    trained_at = timezone.now()
    timestamp = trained_at.strftime('%Y%m%d%H%M%S%f')
    version = f'motif-{timestamp}'
    model_path = model_dir / f'{version}.joblib'
    joblib.dump(pipeline, model_path)
    checksum = hashlib.sha256(model_path.read_bytes()).hexdigest()

    with transaction.atomic():
        ModeleML.objects.filter(actif=True).update(actif=False)
        model = ModeleML.objects.create(
            nom_modele='Suggestion de motif BAM Supervise',
            algorithme='Régression logistique + DictVectorizer',
            version_modele=version,
            entraine_le=trained_at,
            nb_exemples=len(examples),
            metriques=metrics,
            chemin_fichier=str(model_path),
            checksum_sha256=checksum,
            actif=True,
        )
    return model


def safe_maybe_retrain_model():
    try:
        return maybe_retrain_model()
    except Exception:
        logger.exception('Le réentraînement ML BAM Supervise a échoué.')
        return None
