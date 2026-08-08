import os
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from supervision.models import Superviseur


class SupervisorBootstrapSecurityTests(TestCase):
    def test_placeholder_password_is_rejected(self):
        with patch.dict(os.environ, {
            'DJANGO_SUPERVISEUR_USERNAME': 'superviseur',
            'DJANGO_SUPERVISEUR_PASSWORD': 'change-me-now',
            'DJANGO_SUPERVISEUR_EMAIL': 'superviseur@example.com',
            'DJANGO_SUPERVISEUR_NOM': 'Superviseur BAM',
        }, clear=False):
            with self.assertRaises(CommandError):
                call_command('ensure_supervisor')
        self.assertFalse(Superviseur.objects.exists())

    def test_policy_compliant_password_creates_argon2_supervisor(self):
        with patch.dict(os.environ, {
            'DJANGO_SUPERVISEUR_USERNAME': 'superviseur',
            'DJANGO_SUPERVISEUR_PASSWORD': 'BAM-Strong-Initial-Password-2026!',
            'DJANGO_SUPERVISEUR_EMAIL': 'superviseur@example.com',
            'DJANGO_SUPERVISEUR_NOM': 'Superviseur BAM',
        }, clear=False):
            call_command('ensure_supervisor')

        user = Superviseur.objects.get(username='superviseur')
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.password.startswith('argon2$'))
