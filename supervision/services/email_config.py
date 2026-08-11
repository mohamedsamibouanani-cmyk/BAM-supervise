from email.utils import parseaddr

from django.conf import settings
from django.core.validators import validate_email
from django.core.exceptions import ValidationError


class EmailConfigurationError(ValueError):
    """Raised before SMTP when the real notification channel is not ready."""


SIMULATED_EMAIL_BACKENDS = {
    'django.core.mail.backends.console.EmailBackend',
    'django.core.mail.backends.locmem.EmailBackend',
    'django.core.mail.backends.dummy.EmailBackend',
    'django.core.mail.backends.filebased.EmailBackend',
}

PLACEHOLDER_DOMAINS = {
    'localhost',
    'example.com',
    'example.local',
    'example.org',
    'example.net',
    'votre-domaine-bam.ma',
}

PLACEHOLDER_PASSWORDS = {
    '',
    '<secret-smtp>',
    'change-me',
    'change-this',
    'put-google-app-password-here-locally',
}


def validate_real_email_config():
    """Validate delivery settings without opening a network connection."""
    if settings.EMAIL_BACKEND in SIMULATED_EMAIL_BACKENDS and not getattr(
        settings, 'EMAIL_ALLOW_SIMULATED_DELIVERY', False
    ):
        raise EmailConfigurationError(
            'La messagerie est encore en mode test. Configurez un serveur SMTP réel '
            'pour envoyer les notifications aux collaborateurs.'
        )

    if settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
        return

    if not settings.EMAIL_HOST or settings.EMAIL_HOST.lower() == 'localhost':
        raise EmailConfigurationError('EMAIL_HOST doit désigner le serveur SMTP réel.')
    if not settings.EMAIL_HOST_USER:
        raise EmailConfigurationError('EMAIL_HOST_USER doit contenir le compte expéditeur SMTP.')
    if settings.EMAIL_HOST_PASSWORD.strip().lower() in PLACEHOLDER_PASSWORDS:
        raise EmailConfigurationError(
            'EMAIL_HOST_PASSWORD n’est pas configuré. Ajoutez dans .env le mot de passe '
            'd’application Google du compte BAM Supervise, jamais le mot de passe normal.'
        )

    sender = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    if not sender:
        raise EmailConfigurationError(
            'EMAIL_FROM_ADDRESS / DEFAULT_FROM_EMAIL n’est pas configuré correctement.'
        )
    try:
        validate_email(sender)
    except ValidationError as exc:
        raise EmailConfigurationError('L’adresse d’expéditeur est invalide.') from exc

    domain = sender.rsplit('@', 1)[-1].lower()
    if domain in PLACEHOLDER_DOMAINS or domain.endswith('.example'):
        raise EmailConfigurationError(
            'L’adresse d’expéditeur est encore une valeur de démonstration.'
        )

    if settings.EMAIL_HOST.lower() in {'smtp.gmail.com', 'smtp.googlemail.com'}:
        if not settings.EMAIL_USE_TLS or settings.EMAIL_USE_SSL or settings.EMAIL_PORT != 587:
            raise EmailConfigurationError(
                'Pour smtp.gmail.com, utilisez EMAIL_PORT=587, EMAIL_USE_TLS=1 '
                'et EMAIL_USE_SSL=0.'
            )
        if sender.lower() != settings.EMAIL_HOST_USER.lower():
            raise EmailConfigurationError(
                'Avec Gmail SMTP, EMAIL_FROM_ADDRESS doit correspondre à EMAIL_HOST_USER.'
            )
