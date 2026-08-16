from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from supervision.models import (
    Anomalie, CampagneSupervision, ContactGroupe, GroupeResponsable, Motif,
    Notification, NotificationDestinataire, Systeme, Superviseur, ValidationMotif,
)
from supervision.services.notification_retry import retry_failed_notification
from supervision.services.notifications import RoutingError, send_validation_email


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
    DEFAULT_FROM_EMAIL='notifications@bam-supervise.ma',
)
class PersonalizedNotificationTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='personalized-mail', password='Strong-Test-Password-2026!'
        )
        self.smi = Systeme.objects.create(
            code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1
        )
        self.sibo = Systeme.objects.create(
            code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=2
        )
        self.group = GroupeResponsable.objects.create(
            systeme=self.smi, nom_groupe='Groupe SMI'
        )
        self.ahmed = ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Ahmed Benali',
            email='ahmed@example.ma',
        )
        self.fatima = ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Fatima El Amrani',
            email='fatima@example.ma',
        )
        self.motif = Motif.objects.create(
            code_motif='TEST_PERSONNALISATION',
            libelle='Champ à corriger',
            niveau_applicable='ENVOI',
            categorie='TEST',
            champ_typique='VILLE_DESTINATION',
        )
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='PERSONAL-001',
            systeme_ecart=self.sibo,
            empreinte_anomalie='p' * 64,
        )
        self.validation = ValidationMotif.objects.create(
            anomalie=self.anomaly,
            motif_final=self.motif,
            systeme_a_corriger_final=self.smi,
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
            est_finale=True,
        )

    def test_each_collaborator_receives_an_individual_named_email(self):
        notification = send_validation_email(self.validation)

        self.assertEqual(notification.statut, Notification.Statut.ENVOYEE)
        self.assertEqual(notification.destinataires.count(), 2)
        self.assertEqual(len(mail.outbox), 2)

        messages = {message.to[0]: message for message in mail.outbox}
        self.assertEqual(set(messages), {'ahmed@example.ma', 'fatima@example.ma'})
        self.assertEqual(messages['ahmed@example.ma'].to, ['ahmed@example.ma'])
        self.assertEqual(messages['fatima@example.ma'].to, ['fatima@example.ma'])
        self.assertTrue(messages['ahmed@example.ma'].body.startswith('Bonjour Ahmed Benali,\n\n'))
        self.assertTrue(messages['fatima@example.ma'].body.startswith('Bonjour Fatima El Amrani,\n\n'))
        self.assertIn('Bonjour [Nom du collaborateur],', notification.message)
        self.assertFalse('fatima@example.ma' in messages['ahmed@example.ma'].to)
        self.assertFalse('ahmed@example.ma' in messages['fatima@example.ma'].to)
        self.assertFalse(
            notification.destinataires.exclude(
                statut_livraison=NotificationDestinataire.Statut.ENVOYE
            ).exists()
        )

    def test_retry_only_targets_the_recipient_that_failed(self):
        def selective_send(message, fail_silently=False):
            if message.to == ['fatima@example.ma']:
                raise RuntimeError('SMTP temporairement indisponible')
            return 1

        with patch(
            'supervision.services.notifications.EmailMessage.send',
            autospec=True,
            side_effect=selective_send,
        ):
            with self.assertRaises(RoutingError):
                send_validation_email(self.validation)

        failed_notification = Notification.objects.get()
        self.assertEqual(failed_notification.statut, Notification.Statut.ECHEC)
        self.assertEqual(
            failed_notification.destinataires.get(
                email_snapshot='ahmed@example.ma'
            ).statut_livraison,
            NotificationDestinataire.Statut.ENVOYE,
        )
        self.assertEqual(
            failed_notification.destinataires.get(
                email_snapshot='fatima@example.ma'
            ).statut_livraison,
            NotificationDestinataire.Statut.ECHEC,
        )

        mail.outbox.clear()
        retried = retry_failed_notification(failed_notification)

        self.assertEqual(retried.statut, Notification.Statut.ENVOYEE)
        self.assertEqual(retried.destinataires.count(), 1)
        recipient = retried.destinataires.get()
        self.assertEqual(recipient.email_snapshot, 'fatima@example.ma')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['fatima@example.ma'])
        self.assertTrue(mail.outbox[0].body.startswith('Bonjour Fatima El Amrani,\n\n'))
