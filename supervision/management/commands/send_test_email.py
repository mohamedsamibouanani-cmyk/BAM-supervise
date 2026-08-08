from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError


PLACEHOLDER_DOMAINS = {
    'localhost',
    'example.com',
    'example.local',
    'example.org',
    'example.net',
    'votre-domaine-bam.ma',
}


class Command(BaseCommand):
    help = 'Envoie un e-mail de test avec l’identité professionnelle configurée pour BAM Supervise.'

    def add_arguments(self, parser):
        parser.add_argument('--to', required=True, help='Adresse destinataire du message de test.')

    def handle(self, *args, **options):
        recipient = options['to'].strip()
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise CommandError('Adresse destinataire invalide.') from exc

        if settings.EMAIL_BACKEND.endswith('console.EmailBackend'):
            raise CommandError(
                'EMAIL_BACKEND utilise encore le backend console. Configurez un serveur SMTP réel dans .env.'
            )

        sender = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
        if not sender:
            raise CommandError('EMAIL_FROM_ADDRESS / DEFAULT_FROM_EMAIL n’est pas configuré correctement.')
        try:
            validate_email(sender)
        except ValidationError as exc:
            raise CommandError('L’adresse professionnelle d’expéditeur est invalide.') from exc

        domain = sender.rsplit('@', 1)[-1].lower()
        if domain in PLACEHOLDER_DOMAINS or domain.endswith('.example'):
            raise CommandError(
                'L’adresse d’expéditeur est encore une valeur de démonstration. '
                'Configurez la vraie boîte professionnelle BAM Supervise.'
            )

        reply_to = [settings.EMAIL_REPLY_TO] if settings.EMAIL_REPLY_TO else None
        try:
            sent = EmailMessage(
                subject='[BAM Supervise] Test de messagerie',
                body=(
                    'Bonjour,\n\n'
                    'Ceci est un message de test envoyé par BAM Supervise.\n'
                    'La configuration SMTP et l’identité professionnelle de l’application sont opérationnelles.\n\n'
                    'BAM Supervise'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
                reply_to=reply_to,
            ).send(fail_silently=False)
        except Exception as exc:
            raise CommandError(f'Échec SMTP : {exc}') from exc

        if sent != 1:
            raise CommandError('Le backend e-mail n’a pas confirmé l’envoi du message de test.')

        self.stdout.write(self.style.SUCCESS(
            f'E-mail de test envoyé à {recipient} depuis {settings.DEFAULT_FROM_EMAIL}.'
        ))
