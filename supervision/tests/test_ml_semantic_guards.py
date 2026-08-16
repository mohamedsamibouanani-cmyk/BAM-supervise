from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from supervision.models import (
    Anomalie, AttributDefinition, CampagneSupervision, ExempleApprentissage,
    ModeleML, Motif, PredictionMotif, Systeme, Superviseur, ValidationMotif,
)
from supervision.services.ml_inference import ensure_ml_predictions
from supervision.services.ml_training import training_status


class _FakePipeline:
    classes_ = ['ATTRIBUT_DIFFERENT', 'FORMAT_ATTRIBUT_INCOMPATIBLE']

    def predict_proba(self, rows):
        return [[0.80, 0.20]]


class MLSemanticGuardTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-semantic', password='Strong-Test-Password-2026!'
        )
        self.sicom = Systeme.objects.create(
            code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.attribute = AttributDefinition.objects.create(
            code_attribut='TELEPHONE_NOTIFICATION',
            libelle='Téléphone de notification',
            portee='SERVICE',
            type_valeur='TEXTE',
        )
        self.generic_absent = Motif.objects.create(
            code_motif='ATTRIBUT_NON_SYNCHRONISE',
            libelle='Valeur d’attribut absente / non synchronisée',
            niveau_applicable='ATTRIBUT', categorie='SYNCHRONISATION',
        )
        self.generic_different = Motif.objects.create(
            code_motif='ATTRIBUT_DIFFERENT',
            libelle='Valeur d’attribut différente / non synchronisée',
            niveau_applicable='ATTRIBUT', categorie='SYNCHRONISATION',
        )
        self.causal = Motif.objects.create(
            code_motif='FORMAT_ATTRIBUT_INCOMPATIBLE',
            libelle='Format d’attribut potentiellement incompatible',
            niveau_applicable='ATTRIBUT', categorie='FORMAT',
        )

    def _anomaly(self, type_ecart='ABSENT'):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=type_ecart,
            code_envoi=f'ML-SEM-{type_ecart}',
            code_service='30100',
            attribut=self.attribute,
            systeme_ecart=self.sicom if type_ecart == 'ABSENT' else None,
            empreinte_anomalie=(type_ecart + '0' * 64)[:64],
            statut=Anomalie.Statut.ANALYSEE,
        )

    def _learning_example(self, motif, index):
        anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi=f'LEARN-{index}',
            code_service='30100',
            attribut=self.attribute,
            systeme_ecart=self.sicom,
            empreinte_anomalie=f'{index:064x}',
        )
        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            motif_final=motif,
            systeme_a_corriger_final=self.sicom,
            superviseur=self.user,
            decision=ValidationMotif.Decision.MODIFIE,
            version_validation=1,
            est_finale=True,
        )
        return ExempleApprentissage.objects.create(
            validation=validation,
            motif_label=motif,
            systeme_a_corriger_label=self.sicom,
            caracteristiques={
                'niveau': 'ATTRIBUT',
                'type_ecart': 'ABSENT',
                'service': '30100',
                'attribut': 'TELEPHONE_NOTIFICATION',
                'systeme_ecart': 'SICOM',
            },
            eligible=True,
        )

    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    @patch('supervision.services.ml_inference.joblib.load', return_value=_FakePipeline())
    def test_1_ml_does_not_repeat_the_structural_attribute_constat(self, _load, _exists):
        ModeleML.objects.create(
            nom_modele='Test', algorithme='Test', version_modele='semantic-v1',
            entraine_le=timezone.now(), nb_exemples=6, metriques={},
            chemin_fichier='/tmp/semantic-v1.joblib', checksum_sha256='a' * 64,
            actif=True,
        )
        anomaly = self._anomaly(Anomalie.TypeEcart.DIFFERENT)

        created = ensure_ml_predictions(anomaly)

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].motif, self.causal)
        self.assertFalse(
            anomaly.predictions.filter(
                source_prediction=PredictionMotif.Source.ML,
                motif=self.generic_different,
            ).exists()
        )

    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    @patch('supervision.services.ml_inference.joblib.load', return_value=_FakePipeline())
    def test_2_ml_never_decides_the_system_to_correct(self, _load, _exists):
        ModeleML.objects.create(
            nom_modele='Test', algorithme='Test', version_modele='semantic-v2',
            entraine_le=timezone.now(), nb_exemples=6, metriques={},
            chemin_fichier='/tmp/semantic-v2.joblib', checksum_sha256='b' * 64,
            actif=True,
        )
        anomaly = self._anomaly(Anomalie.TypeEcart.ABSENT)

        created = ensure_ml_predictions(anomaly)

        self.assertEqual(len(created), 1)
        prediction = created[0]
        self.assertIsNone(prediction.systeme_a_corriger_predit)
        self.assertEqual(prediction.explication.get('systemes_a_corriger'), [])
        self.assertTrue(prediction.explication.get('systeme_a_corriger_a_confirmer'))

    def test_3_training_excludes_structural_constats_from_ml_dataset(self):
        self._learning_example(self.generic_absent, 1)
        self._learning_example(self.generic_different, 2)
        self._learning_example(self.causal, 3)

        status = training_status()

        self.assertEqual(status['eligible_examples'], 3)
        self.assertEqual(status['trainable_examples'], 1)
        self.assertEqual(status['excluded_examples'], 2)
        self.assertEqual(status['motif_classes'], 1)
        self.assertFalse(status['ready'])
