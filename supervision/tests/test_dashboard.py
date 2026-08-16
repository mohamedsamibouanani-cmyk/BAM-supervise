from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from supervision.models import Anomalie, CampagneSupervision, Systeme, Superviseur


class OperationalDashboardTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='superviseur-dashboard',
            password='Strong-Test-Password-2026!',
            nom_complet='Superviseur Dashboard',
        )
        self.system = Systeme.objects.create(
            code_systeme='SMI',
            nom_systeme='SMI',
            ordre_comparaison=1,
        )
        self.campaign = CampagneSupervision.objects.create(
            superviseur=self.user,
            statut=CampagneSupervision.Statut.TERMINEE,
        )
        self.client.force_login(self.user)

    def _anomaly(self, code, status):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi=code,
            systeme_ecart=self.system,
            statut=status,
            empreinte_anomalie=(code.lower() * 64)[:64],
        )

    def test_dashboard_leads_with_actionable_work(self):
        oldest = self._anomaly('OLD', Anomalie.Statut.DETECTEE)
        newest = self._anomaly('NEW', Anomalie.Statut.ANALYSEE)
        Anomalie.objects.filter(pk=oldest.pk).update(detectee_le=timezone.now() - timedelta(days=2))
        Anomalie.objects.filter(pk=newest.pk).update(detectee_le=timezone.now() - timedelta(hours=1))

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '2 anomalies à valider')
        self.assertContains(response, 'À traiter en priorité')
        self.assertLess(response.content.index(b'OLD'), response.content.index(b'NEW'))
        self.assertNotContains(response, 'Critiques ouvertes')

    def test_dashboard_has_a_clear_state_when_nothing_needs_validation(self):
        self._anomaly('DONE', Anomalie.Statut.RESOLUE)

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Aucune décision en attente')
        self.assertContains(response, 'File de traitement vide')

    def test_dashboard_trend_compares_detection_and_resolution_on_same_period(self):
        detected = self._anomaly('TREND', Anomalie.Statut.RESOLUE)
        yesterday = timezone.now() - timedelta(days=1)
        Anomalie.objects.filter(pk=detected.pk).update(
            detectee_le=yesterday,
            cloturee_le=timezone.now(),
        )

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['trend_detected_total'], 1)
        self.assertEqual(response.context['trend_resolved_total'], 1)
        self.assertEqual(response.context['trend_balance'], 0)
        self.assertTrue(response.context['trend_has_activity'])
        self.assertEqual(sum(response.context['dashboard_data']['trend']['detected']), 1)
        self.assertEqual(sum(response.context['dashboard_data']['trend']['resolved']), 1)
        self.assertContains(response, 'Activité sur 7 jours')
        self.assertNotContains(response, f'{response.context["total"]} au total')

    def test_dashboard_trend_has_an_explicit_empty_state(self):
        response = self.client.get(reverse('dashboard'))

        self.assertFalse(response.context['trend_has_activity'])
        self.assertContains(response, 'Aucune activité sur cette période')
        self.assertNotContains(response, 'id="trendChart"')
