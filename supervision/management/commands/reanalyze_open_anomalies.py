from django.core.management.base import BaseCommand

from supervision.models import Anomalie
from supervision.services.analysis_runtime import analyze_anomaly


class Command(BaseCommand):
    help = 'Recalcule le diagnostic des anomalies non validées avec le moteur métier courant.'

    def handle(self, *args, **options):
        queryset = Anomalie.objects.filter(
            statut__in=[Anomalie.Statut.DETECTEE, Anomalie.Statut.ANALYSEE]
        ).select_related('attribut', 'systeme_ecart', 'campagne').order_by('pk')

        total = queryset.count()
        for anomaly in queryset.iterator():
            analyze_anomaly(anomaly)

        self.stdout.write(
            self.style.SUCCESS(f'{total} anomalie(s) ouverte(s) réanalysée(s).')
        )
