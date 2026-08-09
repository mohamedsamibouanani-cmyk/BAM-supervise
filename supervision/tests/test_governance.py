from django.test import TestCase, override_settings
from django.urls import reverse

from supervision.models import (
    Anomalie, CampagneSupervision, ContactGroupe, GroupeResponsable,
    JournalAudit, Motif, Notification, Systeme, Superviseur, ValidationMotif,
)


class GovernanceTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='superviseur-test',
            password='Strong-Test-Password-2026!',
            nom_complet='Superviseur Test',
        )
        self.system = Systeme.objects.create(
            code_systeme='SMI',
            nom_systeme='SMI',
            ordre_comparaison=1,
        )
        self.group = GroupeResponsable.objects.create(
            systeme=self.system,
            nom_groupe='Groupe SMI',
        )
        self.contact = ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Collaborateur SMI',
            email='collaborateur@example.ma',
            fonction='Support',
        )

    def test_activity_page_requires_authentication(self):
        response = self.client.get(reverse('activity_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_activity_log_is_visible_and_filterable_to_supervisor(self):
        JournalAudit.objects.create(
            superviseur=self.user,
            action='UPDATE',
            entite='contact_groupe',
            id_entite=self.contact.pk,
            nouvelles_valeurs={'email': self.contact.email},
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('activity_list'), {'q': 'contact_groupe'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Activités')
        self.assertContains(response, 'contact_groupe')
        self.assertContains(response, 'UPDATE')
        self.assertContains(response, 'Export CSV')

    def test_activity_csv_export(self):
        JournalAudit.objects.create(
            superviseur=self.user,
            action='CREATE',
            entite='contact_groupe',
            id_entite=self.contact.pk,
            nouvelles_valeurs={'email': self.contact.email},
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('activity_list'), {'export': 'csv'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('bam-supervise-activites.csv', response['Content-Disposition'])
        self.assertIn('contact_groupe', response.content.decode('utf-8-sig'))

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        EMAIL_ALLOW_SIMULATED_DELIVERY=True,
        DEFAULT_FROM_EMAIL='bam-supervise-test@example.ma',
    )
    def test_failed_notification_retry_preserves_original_failure(self):
        motif = Motif.objects.create(
            code_motif='TEST_RETRY',
            libelle='Motif test relance',
            niveau_applicable='ENVOI',
            categorie='TEST',
        )
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        anomaly = Anomalie.objects.create(
            campagne=campaign,
            niveau='ENVOI',
            type_ecart='ABSENT',
            code_envoi='RETRY001',
            systeme_ecart=self.system,
            empreinte_anomalie='r' * 64,
            statut=Anomalie.Statut.VALIDEE,
        )
        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            motif_final=motif,
            systeme_a_corriger_final=self.system,
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
        )
        failed = Notification.objects.create(
            validation=validation,
            groupe=self.group,
            objet='Échec initial',
            message='Message initial',
            statut=Notification.Statut.ECHEC,
            nb_tentatives=1,
            erreur='SMTP indisponible',
        )

        self.client.force_login(self.user)
        url = reverse('notification_retry', args=[failed.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)

        failed.refresh_from_db()
        self.assertEqual(failed.statut, Notification.Statut.ECHEC)
        self.assertEqual(failed.erreur, 'SMTP indisponible')
        self.assertEqual(Notification.objects.filter(validation=validation).count(), 2)
        retried = Notification.objects.filter(validation=validation).exclude(pk=failed.pk).get()
        self.assertEqual(retried.statut, Notification.Statut.ENVOYEE)
        self.assertTrue(
            JournalAudit.objects.filter(
                action='RETRY_EMAIL',
                entite='notification',
                id_entite=failed.pk,
            ).exists()
        )
