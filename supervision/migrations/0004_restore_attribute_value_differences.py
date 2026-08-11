from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('supervision', '0003_presence_only_anomalies'),
    ]

    operations = [
        migrations.AlterField(
            model_name='anomalie',
            name='type_ecart',
            field=models.CharField(
                choices=[
                    ('ABSENT', 'Absent'),
                    ('DIFFERENT', 'Valeur différente'),
                ],
                max_length=20,
            ),
        ),
    ]
