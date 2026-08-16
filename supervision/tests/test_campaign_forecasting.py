from django.test import TestCase

from supervision.models import (
    Anomalie, AttributDefinition, CampagneSupervision, ExempleApprentissage,
    ModeleML, PredictionMotif, Systeme, Superviseur,
)
from supervision.services.campaign_forecasting import (
    MIN_CAMPAIGNS_FOR_FORECAST,
    campaign_forecast,
)


class CampaignForecastingTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='forecast-supervisor', password='Strong-Test-Password-2026!'
        )
        self.smi = Systeme.objects.create(
            code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1
        )
        self.sicom = Systeme.objects.create(
            code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2
        )
        self.sibo = Systeme.objects.create(
            code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3
        )
        self.phone = AttributDefinition.objects.create(
            code_attribut='TELEPHONE_NOTIFICATION',
            libelle='Téléphone de notification',
            portee='SERVICE', type_valeur='TEXTE', sensible=True,
        )

    def _campaign(self, index, envoi=0, service=0, attr_absent=0, attr_diff=0):
        campaign = CampagneSupervision.objects.create(
            superviseur=self.user,
            statut=CampagneSupervision.Statut.TERMINEE,
            nb_anomalies=envoi + service + attr_absent + attr_diff,
        )
        seq = 0

        def add(level, type_ecart, system, attribute=None):
            nonlocal seq
            seq += 1
            Anomalie.objects.create(
                campagne=campaign,
                niveau=level,
                type_ecart=type_ecart,
                code_envoi=f'C{index:02d}-{seq:03d}',
                code_service='30100' if level != Anomalie.Niveau.ENVOI else '',
                attribut=attribute,
                systeme_ecart=system,
                empreinte_anomalie=f'{index:02x}{seq:02x}'.ljust(64, '0'),
            )

        for _ in range(envoi):
            add(Anomalie.Niveau.ENVOI, Anomalie.TypeEcart.ABSENT, self.sibo)
        for _ in range(service):
            add(Anomalie.Niveau.SERVICE, Anomalie.TypeEcart.ABSENT, self.sicom)
        for _ in range(attr_absent):
            add(Anomalie.Niveau.ATTRIBUT, Anomalie.TypeEcart.ABSENT, self.sicom, self.phone)
        for _ in range(attr_diff):
            add(Anomalie.Niveau.ATTRIBUT, Anomalie.TypeEcart.DIFFERENT, self.sibo, self.phone)
        return campaign

    def test_iteration_1_deux_campagnes_restent_insuffisantes(self):
        for index in range(1, 3):
            self._campaign(index, envoi=1, attr_diff=index)

        result = campaign_forecast()

        self.assertFalse(result['ready'])
        self.assertEqual(result['campaign_count'], 2)
        self.assertEqual(result['minimum_campaigns'], 3)
        self.assertEqual(result['minimum_campaigns'], MIN_CAMPAIGNS_FOR_FORECAST)
        self.assertEqual(result['remaining_campaigns'], 1)
        self.assertEqual(result['method'], 'Random Forest + Régression logistique')
        self.assertEqual(ModeleML.objects.count(), 0)
        self.assertEqual(PredictionMotif.objects.count(), 0)

    def test_iteration_2_trois_campagnes_produisent_les_deux_modeles(self):
        patterns = [
            (4, 1, 1, 0),
            (1, 1, 1, 5),
            (5, 1, 1, 0),
        ]
        for index, values in enumerate(patterns, start=1):
            self._campaign(index, *values)

        result = campaign_forecast()

        self.assertTrue(result['ready'])
        self.assertEqual(result['campaign_count'], 3)
        self.assertEqual(result['forecast_maturity'], 'EXPLORATOIRE')
        self.assertGreaterEqual(result['predicted_total'], 0)
        self.assertEqual(len(result['distribution']), 4)
        self.assertEqual(
            sum(row['count'] for row in result['distribution']),
            result['predicted_total'],
        )
        self.assertIn(result['trend'], {'HAUSSE', 'BAISSE', 'STABLE'})
        self.assertEqual(result['historical_dominant_attribute'], 'TELEPHONE_NOTIFICATION')
        self.assertTrue(result['classification_ready'])
        self.assertIsNotNone(result['classification_probability'])
        self.assertGreaterEqual(result['classification_probability'], 0)
        self.assertLessEqual(result['classification_probability'], 100)
        self.assertIn(
            result['classification_dominant_type'],
            {'Envoi absent', 'Valeur d’attribut différente'},
        )
        self.assertGreaterEqual(len(result['classification_distribution']), 2)

    def test_iteration_3_le_ml_previsionnel_ne_touche_jamais_au_workflow_anomalie(self):
        for index in range(1, 4):
            self._campaign(index, envoi=index % 2, service=1, attr_absent=1, attr_diff=2)

        before_anomalies = Anomalie.objects.count()
        result = campaign_forecast()

        self.assertTrue(result['ready'])
        self.assertEqual(Anomalie.objects.count(), before_anomalies)
        self.assertEqual(ModeleML.objects.count(), 0)
        self.assertEqual(PredictionMotif.objects.count(), 0)
        self.assertEqual(ExempleApprentissage.objects.count(), 0)

    def test_iteration_4_logistique_reste_optionnelle_si_une_seule_classe_domine(self):
        for index in range(1, 4):
            self._campaign(index, envoi=0, service=0, attr_absent=1, attr_diff=4)

        result = campaign_forecast()

        self.assertTrue(result['ready'])
        self.assertFalse(result['classification_ready'])
        self.assertIsNone(result['classification_probability'])
        self.assertEqual(result['dominant_type'], "Valeur d’attribut différente")
