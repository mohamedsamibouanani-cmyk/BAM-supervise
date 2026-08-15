from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ExempleApprentissage
from .services.ml_training import safe_maybe_retrain_model


@receiver(post_save, sender=ExempleApprentissage)
def retrain_after_learning_example(sender, instance, created, **kwargs):
    if not created or not instance.eligible:
        return
    # Le callback s'exécute seulement après validation définitive de la transaction.
    transaction.on_commit(safe_maybe_retrain_model)
