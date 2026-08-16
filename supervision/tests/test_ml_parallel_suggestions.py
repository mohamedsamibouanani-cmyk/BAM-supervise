from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from supervision.models import (
    Anomalie, CampagneSupervision, ModeleML, Motif, PredictionMotif,
    RegleMetier, Systeme, Superviseur,
)
from supervision.services.ml_inference import ensure_ml_predictions


class FakeProbabilityPipeline:
    classes_ = ['ML_CAUSE_A', 'ML_CAUSE_B']

    def predict_proba(self, rows):
        return [[0.72, 0.28]]


class MLParallelSuggestionTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-parallel', password='Strong-Test-Password-2026!'
        )
        self.system = Systeme.objects.create(
            code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.motif_rule = Motif.objects.create(
            code_motif='RULE_CAUSE', libelle='Cause règle',
            niveau_applicable='ENVOI', categorie='TEST'
        )
        self.motif_a = Motif.objects.create(
            code_motif='ML_CAUSE_A', libelle='Cause ML A',
            niveau_applicable='ENVOI', categorie='TEST'
        )
        self.motif_b = Motif.objects.create(
            code_motif='ML_CAUSE_B', libelle='Cause ML B',
            niveau_applicable='ENVOI', categorie='TEST'
        )
        self.model = ModeleML.objects.create(
            nom_modele='Suggestion de motif BAM Supervise',
            algorithme='Régression logistique + DictVectorizer',
            version_modele='parallel-v1',
            entraine_le=timezone.now(),
            nb_exemples=11,
            metriques={'nb_classes': 2},
            chemin_fichier='/tmp/parallel-v1.joblib',
            checksum_sha256='f' * 64,
            actif=True,
        )

    def _anomaly(self, code='ML-PARALLEL-001', level=Anomalie.Niveau.ENVOI):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau=level,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi=code,
            code_service='SVC-TEST' if level == Anomalie.Niveau.SERVICE else '',
            systeme_ecart=self.system,
            empreinte_anomalie=(code.replace('-', '') + '0' * 64)[:64],
        )

    @patch('supervision.services.ml_inference.joblib.load', return_value=FakeProbabilityPipeline())
    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    def test_1_ml_is_added_even_when_rule_already_exists(self, _exists, _load):
        anomaly = self._anomaly()
        rule = RegleMetier.objects.create(
            code_regle='RULE_PARALLEL', motif_suggere=self.motif_rule,
            niveau_anomalie='ENVOI', type_controle='AUTRE',
            expression_regle={}, seuil_confiance=Decimal('0.9500'),
            priorite=1, actif=True,
        )
        PredictionMotif.objects.create(
            anomalie=anomaly,
            source_prediction=PredictionMotif.Source.REGLE,
            regle=rule,
            motif=self.motif_rule,
            systeme_a_corriger_predit=self.system,
            rang=1,
            score_confiance=Decimal('0.9500'),
            explication={'message': 'Cause règle'},
        )

        created = ensure_ml_predictions(anomaly)

        self.assertEqual(len(created), 2)
        self.assertEqual(
            anomaly.predictions.filter(source_prediction=PredictionMotif.Source.ML).count(),
            2,
        )
        top_ml = anomaly.predictions.filter(
            source_prediction=PredictionMotif.Source.ML
        ).order_by('rang').first()
        self.assertEqual(top_ml.motif, self.motif_a)
        self.assertEqual(top_ml.score_confiance, Decimal('0.7200'))
        self.assertTrue(
            anomaly.predictions.filter(source_prediction=PredictionMotif.Source.REGLE).exists()
        )

    @patch('supervision.services.ml_inference.joblib.load', return_value=FakeProbabilityPipeline())
    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    def test_2_three_repeated_calls_do_not_duplicate_ml_predictions(self, _exists, _load):
        anomaly = self._anomaly(code='ML-PARALLEL-002')

        ensure_ml_predictions(anomaly)
        ensure_ml_predictions(anomaly)
        ensure_ml_predictions(anomaly)

        self.assertEqual(
            anomaly.predictions.filter(source_prediction=PredictionMotif.Source.ML).count(),
            2,
        )

    @patch('supervision.services.ml_inference.joblib.load', return_value=FakeProbabilityPipeline())
    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    def test_3_service_level_remains_without_ml_motif(self, _exists, _load):
        anomaly = self._anomaly(
            code='ML-PARALLEL-003', level=Anomalie.Niveau.SERVICE
        )

        created = ensure_ml_predictions(anomaly)

        self.assertEqual(created, [])
        self.assertFalse(
            anomaly.predictions.filter(source_prediction=PredictionMotif.Source.ML).exists()
        )
