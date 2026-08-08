from django.core.management.base import BaseCommand
from django.db import transaction

from supervision.models import DetailComparaison, ValeurAttributSnapshot
from supervision.services.security import encrypt_sensitive, masked_sensitive_value, sensitive_fingerprint


class Command(BaseCommand):
    help = 'Protège les valeurs historiques des attributs marqués sensibles.'

    @transaction.atomic
    def handle(self, *args, **options):
        protected = 0
        for row in ValeurAttributSnapshot.objects.select_related('attribut').filter(attribut__sensible=True):
            raw = row.valeur_brute or ''
            comparable = row.valeur_normalisee or ''
            changed = False
            if raw and not raw.startswith('enc:v1:'):
                row.valeur_brute = encrypt_sensitive(raw)
                changed = True
            if comparable and not comparable.startswith('hmac:v1:'):
                row.valeur_normalisee = sensitive_fingerprint(comparable)
                changed = True
            if changed:
                row.save(update_fields=['valeur_brute', 'valeur_normalisee'])
                protected += 1

        masked = 0
        details = DetailComparaison.objects.select_related('anomalie__attribut').filter(anomalie__attribut__sensible=True)
        for detail in details:
            raw = masked_sensitive_value(detail.valeur_brute)
            comparable = masked_sensitive_value(detail.valeur_normalisee)
            if detail.valeur_brute != raw or detail.valeur_normalisee != comparable:
                detail.valeur_brute = raw
                detail.valeur_normalisee = comparable
                detail.save(update_fields=['valeur_brute', 'valeur_normalisee'])
                masked += 1

        self.stdout.write(self.style.SUCCESS(
            f'Données sensibles protégées: {protected} snapshot(s), {masked} preuve(s) masquée(s).'
        ))
