from django.db import migrations, models


def preserve_value_difference_anomalies(apps, schema_editor):
    """Keep historical anomalies until 0004 restores their supported type.

    Some DIFFERENT anomalies can already have validated notifications. Deleting
    them here is both unnecessary (0004 restores the choice) and unsafe because
    Notification.validation uses RESTRICT.
    """
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('supervision', '0002_repair_legacy_prepared_campaigns'),
    ]

    operations = [
        migrations.RunPython(preserve_value_difference_anomalies, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='anomalie',
            name='type_ecart',
            field=models.CharField(choices=[('ABSENT', 'Absent')], max_length=20),
        ),
        migrations.AlterField(
            model_name='predictionmotif',
            name='source_prediction',
            field=models.CharField(
                choices=[
                    ('REGLE', 'Règle métier'),
                    ('APPRENTISSAGE', 'Cas appris'),
                    ('ML', 'Machine learning'),
                ],
                max_length=20,
            ),
        ),
    ]
