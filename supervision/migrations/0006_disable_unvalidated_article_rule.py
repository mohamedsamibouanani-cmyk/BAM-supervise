from django.db import migrations


ARTICLE_RULE_CODES = (
    'ENVOI_STRUCTURE_ARTICLE',
    'ENVOI_STRUCTURE_DES_ARTICLE',
)


def disable_unvalidated_article_rule(apps, schema_editor):
    RegleMetier = apps.get_model('supervision', 'RegleMetier')
    Motif = apps.get_model('supervision', 'Motif')
    PredictionMotif = apps.get_model('supervision', 'PredictionMotif')
    ValidationMotif = apps.get_model('supervision', 'ValidationMotif')

    RegleMetier.objects.filter(code_regle__in=ARTICLE_RULE_CODES).update(actif=False)
    Motif.objects.filter(code_motif='ARTICLE_INVALIDE').update(actif=False)

    # Nettoie uniquement les propositions automatiques non retenues. Une prédiction
    # déjà validée par un superviseur est conservée pour l'audit historique.
    retained_prediction_ids = ValidationMotif.objects.filter(
        prediction_retenue__isnull=False,
    ).values_list('prediction_retenue_id', flat=True)
    PredictionMotif.objects.filter(
        regle__code_regle__in=ARTICLE_RULE_CODES,
    ).exclude(pk__in=retained_prediction_ids).delete()


def keep_business_rule_disabled(apps, schema_editor):
    # Une migration inverse ne doit pas réactiver automatiquement une règle métier
    # qui n'a jamais été confirmée. Elle pourra être recréée explicitement si le
    # métier la valide un jour.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('supervision', '0005_mysql_portable_attribute_uniqueness'),
    ]

    operations = [
        migrations.RunPython(
            disable_unvalidated_article_rule,
            keep_business_rule_disabled,
        ),
    ]
