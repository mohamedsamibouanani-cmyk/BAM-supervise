from django.db import migrations
from django.db.models import F


def repair_legacy_prepared_campaigns(apps, schema_editor):
    """Old failed uploads were left as PREPAREE with no associated files."""
    Campaign = apps.get_model('supervision', 'CampagneSupervision')
    legacy_failures = Campaign.objects.filter(statut='PREPAREE', imports__isnull=True)
    legacy_failures.filter(message_erreur='').update(
        message_erreur='Import interrompu avant la validation des fichiers.',
    )
    legacy_failures.update(statut='ECHEC', terminee_le=F('demarree_le'))


class Migration(migrations.Migration):
    dependencies = [
        ('supervision', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(repair_legacy_prepared_campaigns, migrations.RunPython.noop),
    ]
