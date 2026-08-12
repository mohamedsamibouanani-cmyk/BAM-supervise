from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from supervision.models import Anomalie, CampagneSupervision, Systeme, Superviseur


class AnomalyWorkQueueTests(TestCase):
    def setUp(self):
        self.supervisor = Superviseur.objects.create_user(
            username='supervisor-anomalies',
            password='Strong-Test-Password-2026!',
        )
        self.system = Systeme.objects.create(
            code_systeme='SIBO',
            nom_systeme='SIBO',
            ordre_comparaison=3,
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.supervisor)
        self.client.force_login(self.supervisor)

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

    def test_summary_and_quick_views_match_workflow_states(self):
        self._anomaly('DECIDE', Anomalie.Statut.DETECTEE)
        self._anomaly('FOLLOW', Anomalie.Statut.NOTIFIEE)
        self._anomaly('DONE', Anomalie.Statut.RESOLUE)

        response = self.client.get(reverse('anomaly_list'))

        self.assertEqual(response.context['summary'], {
            'total': 3,
            'a_traiter': 1,
            'en_suivi': 1,
            'resolues': 1,
        })
        self.assertContains(response, 'Registre des anomalies')
        self.assertContains(response, 'À traiter')
        self.assertContains(response, 'Analyse en cours')
        self.assertNotContains(response, 'Prototype')
        self.assertNotContains(response, 'Import manuel')

        response = self.client.get(reverse('anomaly_list'), {'vue': 'en_suivi'})
        self.assertContains(response, 'FOLLOW')
        self.assertNotContains(response, 'DECIDE')
        self.assertNotContains(response, 'DONE')

    def test_actionable_items_are_ordered_first_and_oldest_first(self):
        self._anomaly('RESOLVED', Anomalie.Statut.RESOLUE)
        self._anomaly('NEWEST', Anomalie.Statut.ANALYSEE)
        oldest = self._anomaly('OLDEST', Anomalie.Statut.DETECTEE)
        Anomalie.objects.filter(pk=oldest.pk).update(detectee_le=timezone.now() - timedelta(days=2))

        response = self.client.get(reverse('anomaly_list'))
        codes = [anomaly.code_envoi for anomaly in response.context['anomalies']]

        self.assertEqual(codes, ['OLDEST', 'NEWEST', 'RESOLVED'])
        self.assertContains(response, 'Traiter', count=1)
        self.assertContains(response, 'Voir', count=2)

    def test_detected_state_is_kept_technical_not_offered_as_business_filter(self):
        self._anomaly('TECHNICAL', Anomalie.Statut.DETECTEE)

        response = self.client.get(reverse('anomaly_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<option value="DETECTEE"', html=False)
        self.assertContains(response, 'État technique transitoire')

    def test_invalid_choice_is_ignored_safely(self):
        self._anomaly('SAFE', Anomalie.Statut.DETECTEE)

        response = self.client.get(reverse('anomaly_list'), {'statut': 'NOT_A_STATUS'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SAFE')
