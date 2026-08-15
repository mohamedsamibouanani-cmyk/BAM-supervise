from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from supervision.models import ExempleApprentissage
from supervision.services.ml_training import retrain_after_campaign_if_ready


@receiver(post_save, sender=ExempleApprentissage)
def train_model_when_campaign_review_is_complete(sender, instance, created, **kwargs):
    """Train once after the last anomaly of a campaign receives a final validation."""
    if not created:
        return

    campaign_id = instance.validation.anomalie.campagne_id

    def _train_after_commit():
        from supervision.models import CampagneSupervision

        campaign = CampagneSupervision.objects.filter(pk=campaign_id).first()
        if campaign is not None:
            retrain_after_campaign_if_ready(campaign)

    transaction.on_commit(_train_after_commit)
