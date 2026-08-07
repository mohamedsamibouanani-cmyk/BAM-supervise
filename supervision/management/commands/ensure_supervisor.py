import os

from django.core.management.base import BaseCommand

from supervision.models import Superviseur


class Command(BaseCommand):
    help = 'Crée le compte superviseur initial depuis les variables d’environnement, si nécessaire.'

    def handle(self, *args, **options):
        if Superviseur.objects.exists():
            self.stdout.write(self.style.SUCCESS('Le compte superviseur existe déjà.'))
            return

        username = os.getenv('DJANGO_SUPERVISEUR_USERNAME', '').strip()
        password = os.getenv('DJANGO_SUPERVISEUR_PASSWORD', '')
        email = os.getenv('DJANGO_SUPERVISEUR_EMAIL', '').strip()

        if not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    'Aucun superviseur créé automatiquement : renseignez '
                    'DJANGO_SUPERVISEUR_USERNAME et DJANGO_SUPERVISEUR_PASSWORD, '
                    'ou lancez python manage.py createsuperuser.'
                )
            )
            return

        Superviseur.objects.create_superuser(
            username=username,
            password=password,
            email=email,
            nom_complet=os.getenv('DJANGO_SUPERVISEUR_NOM', username).strip() or username,
        )
        self.stdout.write(self.style.SUCCESS(f'Superviseur {username} créé.'))
