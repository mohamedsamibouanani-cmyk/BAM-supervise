from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from supervision.models import (
    Anomalie, CampagneSupervision, ContactGroupe, GroupeResponsable,
    Motif, Notification, Systeme, Superviseur,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
    DEFAULT_FROM_EMAIL='BAM Supervise <bam-supervise@example.ma>',
)
class SupervisorSixCyclesEndUserTests(TestCase):
    """Six complete supervisor-facing cycles across SMI, SICOM and SIBO."""

    def setUp(self):
        self.password = 'Strong-Test-Password-2026!'
        self.supervisor = Superviseur.objects.create_user(
            username='superviseur-six-cycles',
            password=self.password,
            nom_complet='Superviseur Recette',
        )
        self.systems = {}
        self.groups = {}
        self.contacts = {}
        for order, code in enumerate(('SMI', 'SICOM', 'SIBO'), start=1):
            system = Systeme.objects.create(
                code_systeme=code,
                nom_systeme=code,
                ordre_comparaison=order,
            )
            group = GroupeResponsable.objects.create(
                systeme=system,
                nom_groupe=f'Groupe {code}',
            )
            contact = ContactGroupe.objects.create(
                groupe=group,
                nom_complet=f'Collaborateur {code}',
                email=f'{code.lower()}@example.ma',
            )
            self.systems[code] = system
            self.groups[code] = group
            self.contacts[code] = contact

        self.motif = Motif.objects.create(
            code_motif='CAUSE_RECETTE_6_CYCLES',
            libelle='Cause confirmée par le superviseur',
            niveau_applicable='ENVOI',
            categorie='APPRENTISSAGE',
        )
        self.client = Client()
        self.assertTrue(
            self.client.login(username=self.supervisor.username, password=self.password)
        )

    def test_six_complete_end_user_cycles(self):
        target_sequence = ('SMI', 'SICOM', 'SIBO', 'SMI', 'SICOM', 'SIBO')

        for cycle, target_code in enumerate(target_sequence, start=1):
            campaign = CampagneSupervision.objects.create(superviseur=self.supervisor)
            observed_code = 'SIBO' if target_code != 'SIBO' else 'SICOM'
            anomaly = Anomalie.objects.create(
                campagne=campaign,
                niveau=Anomalie.Niveau.ENVOI,
                type_ecart=Anomalie.TypeEcart.ABSENT,
                code_envoi=f'RECETTE-{cycle:02d}',
                systeme_ecart=self.systems[observed_code],
                statut=Anomalie.Statut.ANALYSEE,
                empreinte_anomalie=f'{cycle:064x}',
            )

            # Navigation réelle du superviseur avant la décision.
            for route_name in ('dashboard', 'anomaly_list', 'notification_list'):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200, f'Cycle {cycle}: {route_name}')

            detail = self.client.get(reverse('anomaly_detail', args=[anomaly.pk]))
            self.assertEqual(detail.status_code, 200)
            self.assertContains(detail, anomaly.code_envoi)

            validation_page = self.client.get(reverse('anomaly_validate', args=[anomaly.pk]))
            self.assertEqual(validation_page.status_code, 200)
            self.assertContains(validation_page, 'Valider le diagnostic')

            # Un ajustement humain explicite doit toujours router vers le système choisi.
            response = self.client.post(
                reverse('anomaly_validate', args=[anomaly.pk]),
                {
                    'decision': 'MODIFIE',
                    'motif_final': str(self.motif.pk),
                    'systeme_a_corriger_final': str(self.systems[target_code].pk),
                    'commentaire': f'Recette utilisateur cycle {cycle}/6',
                },
            )
            self.assertEqual(response.status_code, 302, f'Cycle {cycle}: validation HTTP')

            anomaly.refresh_from_db()
            self.assertEqual(
                anomaly.statut,
                Anomalie.Statut.NOTIFIEE,
                f'Cycle {cycle}: statut après notification',
            )

            notification = Notification.objects.filter(
                validation__anomalie=anomaly
            ).select_related('groupe__systeme').get()
            self.assertEqual(notification.statut, Notification.Statut.ENVOYEE)
            self.assertEqual(notification.groupe.systeme.code_systeme, target_code)
            self.assertTrue(
                notification.destinataires.filter(
                    email_snapshot=self.contacts[target_code].email
                ).exists()
            )

            message = mail.outbox[-1]
            self.assertEqual(message.to, [self.contacts[target_code].email])
            self.assertIn(anomaly.code_envoi, message.body)
            self.assertIn(f'Système responsable de la correction : {target_code}', message.body)

            journal = self.client.get(reverse('notification_list'))
            self.assertEqual(journal.status_code, 200)
            self.assertContains(journal, anomaly.code_envoi)
            self.assertContains(journal, target_code)

        self.assertEqual(len(mail.outbox), 6)
        self.assertEqual(Notification.objects.filter(statut=Notification.Statut.ENVOYEE).count(), 6)
        self.assertEqual(
            set(Notification.objects.values_list('groupe__systeme__code_systeme', flat=True)),
            {'SMI', 'SICOM', 'SIBO'},
        )
