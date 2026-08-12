import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from supervision.models import Superviseur


_FORBIDDEN_BOOTSTRAP_PASSWORDS = {
    'change-me-now',
    'admin12345',
    'password',
    'superviseur',
}


class Command(BaseCommand):
    help = 'Crée le compte superviseur initial depuis les variables d’environnement, si nécessaire.'

    def handle(self, *args, **options):
        if Superviseur.objects.exists():
            self.stdout.write(self.style.SUCCESS('Le compte superviseur existe déjà.'))
            return

        username = os.getenv('DJANGO_SUPERVISEUR_USERNAME', '').strip()
        password = os.getenv('DJANGO_SUPERVISEUR_PASSWORD', '')
        email = os.getenv('DJANGO_SUPERVISEUR_EMAIL', '').strip()
        nom_complet = os.getenv('DJANGO_SUPERVISEUR_NOM', username).strip() or username

        if not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    'Aucun superviseur créé automatiquement : renseignez '
                    'DJANGO_SUPERVISEUR_USERNAME et DJANGO_SUPERVISEUR_PASSWORD, '
                    'ou lancez python manage.py createsuperuser.'
                )
            )
            return

        if password.lower() in _FORBIDDEN_BOOTSTRAP_PASSWORDS:
            self.stdout.write(
                self.style.WARNING(
                    'Aucun superviseur créé : DJANGO_SUPERVISEUR_PASSWORD utilise une '
                    'valeur de démonstration interdite. Choisissez un mot de passe fort '
                    'et privé, puis relancez le service web.'
                )
            )
            return

        candidate = Superviseur(
            username=username,
            email=email,
            nom_complet=nom_complet,
        )
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            self.stdout.write(
                self.style.WARNING(
                    'Aucun superviseur créé : DJANGO_SUPERVISEUR_PASSWORD ne respecte '
                    'pas la politique de sécurité : ' + ' '.join(exc.messages)
                )
            )
            return

        Superviseur.objects.create_superuser(
            username=username,
            password=password,
            email=email,
            nom_complet=nom_complet,
        )
        self.stdout.write(self.style.SUCCESS(f'Superviseur {username} créé.'))
