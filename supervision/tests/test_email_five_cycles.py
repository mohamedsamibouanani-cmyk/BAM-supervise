from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from supervision.models import (
    Anomalie,
    CampagneSupervision,
    ContactGroupe,
    GroupeResponsable,
    JournalAudit,
    Motif,
    Notification,
    RegleAffectation,
    Systeme,
    Superviseur,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
    EMAIL_FROM_NAME='BAM Supervise',
    EMAIL_FROM_ADDRESS='notifications@bam.test',
    EMAIL_REPLY_TO='support@bam.test',
    DEFAULT_FROM_EMAIL='BAM Supervise <notifications@bam.test>',
)
class FiveCycleSupervisorEmailFlowTests(TestCase):
    RECIPIENT = 'mohamedsami.bouanani@uit.ac.ma'

    def setUp(self):
        self.supervisor = Superviseur.objects.create_user(
            username='superviseur',
            password='Strong-Test-Password-2026!',
            email='superviseur@bam.test',
        )
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        self.motif = Motif.objects.create(
            code_motif='VILLE_MANQUANTE',
            libelle='Ville manquante',
            niveau_applicable='ENVOI',
            categorie='DONNEE',
        )
        self.group = GroupeResponsable.objects.create(systeme=self.smi, nom_groupe='Groupe SMI')
        self.contact = ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Mohamed Sami Bouanani',
            email=self.RECIPIENT,
            actif=True,
        )
        RegleAffectation.objects.create(
            systeme_a_corriger=self.smi,
            motif=self.motif,
            groupe=self.group,
            actif=True,
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.supervisor)
        self.client = Client()
        self.assertTrue(self.client.login(username='superviseur', password='Strong-Test-Password-2026!'))

    def _make_anomaly(self, index):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau='ENVOI',
            type_ecart='ABSENT',
            code_envoi=f'EMAIL-CYCLE-{index:02d}',
            systeme_ecart=self.sibo,
            empreinte_anomalie=f'{index:064x}',
        )

    def test_supervisor_validation_sends_five_notifications_to_real_test_address(self):
        for cycle in range(1, 6):
            anomaly = self._make_anomaly(cycle)
            response = self.client.post(
                reverse('anomaly_validate', args=[anomaly.pk]),
                {
                    'decision': 'ACCEPTE',
                    'motif_final': str(self.motif.pk),
                    'systeme_a_corriger_final': str(self.smi.pk),
                    'commentaire': f'Test e-mail cycle {cycle}/5',
                },
            )
            self.assertEqual(response.status_code, 302, f'Cycle {cycle}: validation HTTP échouée')

            anomaly.refresh_from_db()
            self.assertEqual(anomaly.statut, Anomalie.Statut.NOTIFIEE, f'Cycle {cycle}: anomalie non notifiée')

            notification = Notification.objects.filter(validation__anomalie=anomaly).latest('creee_le')
            self.assertEqual(notification.statut, Notification.Statut.ENVOYEE, f'Cycle {cycle}: notification non envoyée')
            self.assertTrue(
                notification.destinataires.filter(email_snapshot=self.RECIPIENT).exists(),
                f'Cycle {cycle}: destinataire non historisé',
            )

            message = mail.outbox[cycle - 1]
            self.assertIn(self.RECIPIENT, message.to, f'Cycle {cycle}: mauvaise adresse destinataire')
            self.assertEqual(message.from_email, 'BAM Supervise <notifications@bam.test>')
            self.assertEqual(message.reply_to, ['support@bam.test'])
            self.assertIn(f'EMAIL-CYCLE-{cycle:02d}', message.body)
            self.assertIn('Système à corriger : SMI', message.body)

        self.assertEqual(len(mail.outbox), 5)
        self.assertEqual(Notification.objects.filter(statut=Notification.Statut.ENVOYEE).count(), 5)
        self.assertEqual(
            JournalAudit.objects.filter(
                superviseur=self.supervisor,
                action='VALIDATE',
                entite='validation_motif',
            ).count(),
            5,
        )
