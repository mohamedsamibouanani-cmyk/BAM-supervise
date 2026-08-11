from django.db import migrations, models


def remove_value_difference_anomalies(apps, schema_editor):
    Anomalie = apps.get_model('supervision', 'Anomalie')
    Anomalie.objects.filter(type_ecart='DIFFERENT').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('supervision', '0002_repair_legacy_prepared_campaigns'),
    ]

    operations = [
        migrations.RunPython(remove_value_difference_anomalies, migrations.RunPython.noop),
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
