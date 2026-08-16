from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from supervision.models import (
    Anomalie, AttributDefinition, CampagneSupervision, ModeleML, Motif,
    PredictionMotif, Systeme, Superviseur,
)
from supervision.services.ml_inference import ensure_ml_predictions


class FakeAttributePipeline:
    classes_ = ['ATTRIBUT_NON_SYNCHRONISE', 'ATTRIBUT_DIFFERENT']

    def predict_proba(self, rows):
        return [[0.52, 0.48]]


class MLAttributeValueSemanticsTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-attribute-values', password='Strong-Test-Password-2026!'
        )
        self.system = Systeme.objects.create(
            code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.attribute = AttributDefinition.objects.create(
            code_attribut='TELEPHONE_NOTIFICATION',
            libelle='Téléphone de notification',
            portee='SERVICE',
            type_valeur='TEXTE',
            sensible=True,
            actif=True,
        )
        self.motif_absent = Motif.objects.create(
            code_motif='ATTRIBUT_NON_SYNCHRONISE',
            libelle='Valeur d’attribut absente / non synchronisée',
            niveau_applicable='ATTRIBUT', categorie='SYNCHRONISATION', actif=True,
        )
        self.motif_different = Motif.objects.create(
            code_motif='ATTRIBUT_DIFFERENT',
            libelle='Valeur d’attribut différente / non synchronisée',
            niveau_applicable='ATTRIBUT', categorie='SYNCHRONISATION', actif=True,
        )
        self.model = ModeleML.objects.create(
            nom_modele='ML attribut', algorithme='Test', version_modele='attr-v1',
            entraine_le=timezone.now(), nb_exemples=6, metriques={'nb_classes': 2},
            chemin_fichier='/tmp/attr-v1.joblib', checksum_sha256='a' * 64, actif=True,
        )

    def _anomaly(self, gap, code):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=gap,
            code_envoi=code,
            code_service='30100',
            attribut=self.attribute,
            systeme_ecart=self.system if gap == Anomalie.TypeEcart.ABSENT else None,
            empreinte_anomalie=(code.replace('-', '') + '0' * 64)[:64],
        )

    @patch('supervision.services.ml_inference.joblib.load', return_value=FakeAttributePipeline())
    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    def test_iteration_1_absent_ne_propose_pas_different(self, _exists, _load):
        anomaly = self._anomaly(Anomalie.TypeEcart.ABSENT, 'ATTR-ABS-001')
        created = ensure_ml_predictions(anomaly)
        codes = [prediction.motif.code_motif for prediction in created]
        self.assertEqual(codes, ['ATTRIBUT_NON_SYNCHRONISE'])
        self.assertNotIn('ATTRIBUT_DIFFERENT', codes)

    @patch('supervision.services.ml_inference.joblib.load', return_value=FakeAttributePipeline())
    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    def test_iteration_2_different_ne_propose_pas_absent(self, _exists, _load):
        anomaly = self._anomaly(Anomalie.TypeEcart.DIFFERENT, 'ATTR-DIFF-002')
        created = ensure_ml_predictions(anomaly)
        codes = [prediction.motif.code_motif for prediction in created]
        self.assertEqual(codes, ['ATTRIBUT_DIFFERENT'])
        self.assertNotIn('ATTRIBUT_NON_SYNCHRONISE', codes)

    @patch('supervision.services.ml_inference.joblib.load', return_value=FakeAttributePipeline())
    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    def test_iteration_3_ancienne_prediction_incompatible_est_nettoyee(self, _exists, _load):
        anomaly = self._anomaly(Anomalie.TypeEcart.ABSENT, 'ATTR-ABS-003')
        PredictionMotif.objects.create(
            anomalie=anomaly,
            source_prediction=PredictionMotif.Source.ML,
            modele=self.model,
            motif=self.motif_different,
            systeme_a_corriger_predit=self.system,
            rang=1,
            score_confiance=Decimal('0.4800'),
            explication={'origine': 'MODELE_ML_ACTIF'},
        )
        created = ensure_ml_predictions(anomaly)
        self.assertFalse(
            anomaly.predictions.filter(
                source_prediction=PredictionMotif.Source.ML,
                motif=self.motif_different,
            ).exists()
        )
        self.assertEqual([p.motif.code_motif for p in created], ['ATTRIBUT_NON_SYNCHRONISE'])
