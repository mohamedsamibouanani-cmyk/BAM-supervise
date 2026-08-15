from django.test import TestCase
from django.urls import reverse

from supervision.models import Anomalie, CampagneSupervision, Systeme, Superviseur


class AnomalyValidationUiRegressionTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ui-regression-superviseur',
            password='Strong-Test-Password-2026!',
            nom_complet='Superviseur UI',
        )
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='UI-REGRESSION-001',
            systeme_ecart=self.sibo,
            empreinte_anomalie='u' * 64,
            statut=Anomalie.Statut.ANALYSEE,
        )
        self.client.force_login(self.user)

    def test_anomaly_detail_keeps_explainability_sections(self):
        response = self.client.get(reverse('anomaly_detail', args=[self.anomaly.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Preuve de comparaison SMI / SICOM / SIBO')
        self.assertContains(response, 'Diagnostic proposé')
        self.assertContains(response, 'Décision finale du superviseur')
        self.assertContains(response, 'Cycle de vie du dossier')
        self.assertContains(response, 'le système où l’écart est observé n’est pas automatiquement le système à corriger')
        self.assertNotContains(response, '>Gravité<', html=False)
        self.assertNotContains(response, '>Critique<', html=False)

    def test_validation_page_keeps_human_decision_workflow(self):
        response = self.client.get(reverse('anomaly_validate', args=[self.anomaly.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Propositions du moteur d’analyse')
        self.assertContains(response, 'Workflow de décision')
        self.assertContains(response, 'Décision du superviseur')
        self.assertContains(response, 'Décision humaine obligatoire')
        self.assertContains(response, 'Cause validée + système à corriger')
        self.assertNotContains(response, '>Gravité<', html=False)
        self.assertNotContains(response, '>Critique<', html=False)
