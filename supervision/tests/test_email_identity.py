from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class ProfessionalEmailIdentityTests(SimpleTestCase):
    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='BAM Supervise <notifications@bam-supervise.ma>',
        EMAIL_REPLY_TO='support@bam-supervise.ma',
    )
    def test_test_email_uses_professional_sender(self):
        call_command('send_test_email', to='recipient@example.com')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.from_email, 'BAM Supervise <notifications@bam-supervise.ma>')
        self.assertEqual(message.reply_to, ['support@bam-supervise.ma'])
        self.assertEqual(message.to, ['recipient@example.com'])

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='BAM Supervise <bam-supervise@localhost>',
        EMAIL_REPLY_TO='',
    )
    def test_test_email_rejects_placeholder_sender(self):
        with self.assertRaises(CommandError):
            call_command('send_test_email', to='recipient@example.com')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend',
        DEFAULT_FROM_EMAIL='BAM Supervise <notifications@bam-supervise.ma>',
        EMAIL_REPLY_TO='',
    )
    def test_test_email_rejects_console_backend(self):
        with self.assertRaises(CommandError):
            call_command('send_test_email', to='recipient@example.com')
