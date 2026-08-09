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
    EMAIL_FROM_ADDRESS='mohamedsami.bouanani@uit.ac.ma',
    EMAIL_REPLY_TO='mohamedsami.bouanani@uit.ac.ma',
    DEFAULT_FROM_EMAIL='BAM Supervise <mohamedsami.bouanani@uit.ac.ma>',
)
class SupervisorFiveIterationsFunctionalTests(TestCase):
    """Repeat the main supervisor workflow five times to catch state/regression issues."""

    def setUp(self):
        self.password = 'Strong-Test-Password-2026!'
        self.supervisor = Superviseur.objects.create_user(
            username='superviseur-functional',
            password=self.password,
            nom_complet='Superviseur Fonctionnel',
        )
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        self.motif = Motif.objects.create(
            code_motif='VILLE_MANQUANTE_FIVE',
            libelle='Ville manquante',
            niveau_applicable='ENVOI',
            categorie='DONNEE',
        )
        self.group = GroupeResponsable.objects.create(
            systeme=self.smi,
            nom_groupe='Groupe SMI - Test fonctionnel',
        )
        RegleAffectation.objects.create(
            systeme_a_corriger=self.smi,
            motif=self.motif,
            groupe=self.group,
            actif=True,
        )
        self.client = Client()
        self.assertTrue(self.client.login(username=self.supervisor.username, password=self.password))

    def _assert_page(self, name, cycle, **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs or None))
        self.assertEqual(response.status_code, 200, f'Cycle {cycle}: page {name} indisponible')
        return response

    def test_main_supervisor_workflow_runs_five_times(self):
        for cycle in range(1, 6):
            # Navigation accessible au superviseur.
            for page in (
                'dashboard',
                'campaign_list',
                'campaign_create',
                'anomaly_list',
                'notification_list',
                'group_list',
                'activity_list',
            ):
                self._assert_page(page, cycle)

            # Ajout d'un collaborateur sans notion actif/inactif.
            email = f'collaborateur{cycle}@example.ma'
            add_response = self.client.post(
                reverse('contact_add', args=[self.group.pk]),
                {
                    'nom_complet': f'Collaborateur {cycle}',
                    'email': email,
                    'fonction': 'Exploitation',
                },
            )
            self.assertEqual(add_response.status_code, 302, f'Cycle {cycle}: ajout collaborateur échoué')
            contact = ContactGroupe.objects.get(email=email)

            # Modification du collaborateur.
            edit_response = self.client.post(
                reverse('contact_edit', args=[contact.pk]),
                {
                    'nom_complet': f'Collaborateur {cycle} Modifié',
                    'email': email,
                    'fonction': 'Support système',
                },
            )
            self.assertEqual(edit_response.status_code, 302, f'Cycle {cycle}: modification collaborateur échouée')
            contact.refresh_from_db()
            self.assertEqual(contact.fonction, 'Support système')

            # Une anomalie est disponible à l'analyse.
            campaign = CampagneSupervision.objects.create(superviseur=self.supervisor)
            anomaly = Anomalie.objects.create(
                campagne=campaign,
                niveau='ENVOI',
                type_ecart='ABSENT',
                code_envoi=f'FUNCTIONAL-{cycle:02d}',
                systeme_ecart=self.sibo,
                empreinte_anomalie=f'f{cycle:063x}',
            )
            self._assert_page('anomaly_detail', cycle, pk=anomaly.pk)
            self._assert_page('anomaly_validate', cycle, pk=anomaly.pk)

            # Validation humaine du motif + système à corriger => notification groupe SMI.
            validation_response = self.client.post(
                reverse('anomaly_validate', args=[anomaly.pk]),
                {
                    'decision': 'ACCEPTE',
                    'motif_final': str(self.motif.pk),
                    'systeme_a_corriger_final': str(self.smi.pk),
                    'commentaire': f'Itération fonctionnelle {cycle}/5',
                },
            )
            self.assertEqual(validation_response.status_code, 302, f'Cycle {cycle}: validation échouée')

            anomaly.refresh_from_db()
            self.assertEqual(anomaly.statut, Anomalie.Statut.NOTIFIEE, f'Cycle {cycle}: anomalie non notifiée')
            notification = Notification.objects.filter(validation__anomalie=anomaly).latest('creee_le')
            self.assertEqual(notification.statut, Notification.Statut.ENVOYEE, f'Cycle {cycle}: mail non envoyé')
            self.assertTrue(notification.destinataires.filter(email_snapshot=email).exists())

            message = mail.outbox[-1]
            self.assertIn(email, message.to, f'Cycle {cycle}: nouveau collaborateur absent des destinataires')
            self.assertEqual(
                message.from_email,
                'BAM Supervise <mohamedsami.bouanani@uit.ac.ma>',
            )
            self.assertIn('Système à corriger : SMI', message.body)
            self.assertIn('Motif validé : Ville manquante', message.body)

            # Les écrans post-action et le journal restent consultables après chaque cycle.
            self._assert_page('notification_list', cycle)
            activity_response = self.client.get(reverse('activity_list'), {'q': 'validation_motif'})
            self.assertEqual(activity_response.status_code, 200)
            self.assertContains(activity_response, 'VALIDATE')

        self.assertEqual(Notification.objects.filter(statut=Notification.Statut.ENVOYEE).count(), 5)
        self.assertEqual(
            JournalAudit.objects.filter(action='VALIDATE', entite='validation_motif').count(),
            5,
        )
        self.assertEqual(ContactGroupe.objects.filter(groupe=self.group).count(), 5)

        # Export de l'activité après les cinq itérations.
        export_response = self.client.get(reverse('activity_list'), {'export': 'csv'})
        self.assertEqual(export_response.status_code, 200)
        self.assertIn('text/csv', export_response['Content-Type'])
