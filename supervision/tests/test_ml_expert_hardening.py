import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from supervision.models import (
    Anomalie, AttributDefinition, CampagneSupervision, ExempleApprentissage,
    ModeleML, Motif, PredictionMotif, RegleMetier, Systeme, Superviseur,
    ValidationMotif,
)
from supervision.services.ml_inference import ensure_ml_predictions
from supervision.services.ml_training import maybe_retrain_model


class _PipelineWithDuplicateAndCause:
    classes_ = ['CAUSE_DEJA_REGLE', 'CAUSE_ML_NOUVELLE']

    def predict_proba(self, rows):
        return [[0.42, 0.58]]


class _PipelineWithNonCausal:
    classes_ = ['ATTRIBUT_DIFFERENT', 'CAUSE_ML_NOUVELLE']

    def predict_proba(self, rows):
        return [[0.66, 0.34]]


class MLExpertHardeningTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-expert', password='Strong-Test-Password-2026!'
        )
        self.system = Systeme.objects.create(
            code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.attribute = AttributDefinition.objects.create(
            code_attribut='TELEPHONE_NOTIFICATION',
            libelle='Téléphone de notification',
            portee='SERVICE', type_valeur='TEXTE', sensible=True,
        )
        self.rule_cause = Motif.objects.create(
            code_motif='CAUSE_DEJA_REGLE', libelle='Cause déjà trouvée par règle',
            niveau_applicable='ATTRIBUT', categorie='TEST'
        )
        self.ml_cause = Motif.objects.create(
            code_motif='CAUSE_ML_NOUVELLE', libelle='Cause supplémentaire ML',
            niveau_applicable='ATTRIBUT', categorie='TEST'
        )
        self.non_causal = Motif.objects.create(
            code_motif='ATTRIBUT_DIFFERENT', libelle='Valeur différente',
            niveau_applicable='ATTRIBUT', categorie='SYNCHRONISATION'
        )
        self.model = ModeleML.objects.create(
            nom_modele='ML expert', algorithme='Test', version_modele='expert-v1',
            entraine_le=timezone.now(), nb_exemples=6,
            metriques={'class_counts': {'CAUSE_DEJA_REGLE': 4, 'CAUSE_ML_NOUVELLE': 2}},
            chemin_fichier='/tmp/expert-v1.joblib', checksum_sha256='a' * 64,
            actif=True,
        )

    def _anomaly(self, code):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.DIFFERENT,
            code_envoi=code,
            code_service='30100',
            attribut=self.attribute,
            systeme_ecart=None,
            empreinte_anomalie=(code.replace('-', '') + '0' * 64)[:64],
        )

    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    @patch('supervision.services.ml_inference.joblib.load', return_value=_PipelineWithDuplicateAndCause())
    def test_iteration_1_ml_ne_duplique_pas_une_cause_deja_donnee_par_regle(self, _load, _exists):
        anomaly = self._anomaly('ML-EXPERT-001')
        rule = RegleMetier.objects.create(
            code_regle='CAUSE_DEJA_REGLE_RULE', motif_suggere=self.rule_cause,
            niveau_anomalie='ATTRIBUT', type_controle='AUTRE', expression_regle={},
            seuil_confiance=Decimal('1.0000'), priorite=1, actif=True,
        )
        PredictionMotif.objects.create(
            anomalie=anomaly, source_prediction=PredictionMotif.Source.REGLE,
            regle=rule, motif=self.rule_cause, rang=1,
            score_confiance=Decimal('1.0000'), explication={'message': 'Cause règle'},
        )

        created = ensure_ml_predictions(anomaly)

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].motif, self.ml_cause)
        self.assertIsNone(created[0].systeme_a_corriger_predit)
        self.assertEqual(created[0].explication['niveau_confiance'], 'MODEREE')
        self.assertEqual(created[0].explication['nb_exemples_cause'], 2)
        self.assertEqual(created[0].explication['version_modele'], 'expert-v1')

    @patch('supervision.services.ml_inference.Path.exists', return_value=True)
    @patch('supervision.services.ml_inference.joblib.load', return_value=_PipelineWithNonCausal())
    def test_iteration_2_un_constat_technique_n_est_jamais_repropose_par_le_ml(self, _load, _exists):
        anomaly = self._anomaly('ML-EXPERT-002')

        created = ensure_ml_predictions(anomaly)

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].motif, self.ml_cause)
        self.assertFalse(
            anomaly.predictions.filter(
                source_prediction=PredictionMotif.Source.ML,
                motif__code_motif='ATTRIBUT_DIFFERENT',
            ).exists()
        )

    def test_iteration_3_le_modele_conserve_la_repartition_des_causes_et_signale_les_singletons(self):
        motif_a = Motif.objects.create(
            code_motif='CAUSE_TRAIN_A', libelle='Cause A',
            niveau_applicable='ATTRIBUT', categorie='TEST'
        )
        motif_b = Motif.objects.create(
            code_motif='CAUSE_TRAIN_B', libelle='Cause B',
            niveau_applicable='ATTRIBUT', categorie='TEST'
        )
        ModeleML.objects.all().delete()

        for index in range(6):
            motif = motif_a if index < 5 else motif_b
            anomaly = self._anomaly(f'ML-TRAIN-{index}')
            validation = ValidationMotif.objects.create(
                anomalie=anomaly, motif_final=motif,
                systeme_a_corriger_final=self.system,
                superviseur=self.user,
                decision=ValidationMotif.Decision.MODIFIE,
                version_validation=1, est_finale=True,
            )
            ExempleApprentissage.objects.create(
                validation=validation, motif_label=motif,
                systeme_a_corriger_label=self.system,
                caracteristiques={
                    'niveau': 'ATTRIBUT', 'type_ecart': 'DIFFERENT',
                    'service': '30100', 'attribut': 'TELEPHONE_NOTIFICATION',
                    'systeme_ecart': 'SICOM',
                },
                eligible=True,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=Path(tmpdir)):
                model = maybe_retrain_model()

        self.assertIsNotNone(model)
        self.assertEqual(model.metriques['class_counts']['CAUSE_TRAIN_A'], 5)
        self.assertEqual(model.metriques['class_counts']['CAUSE_TRAIN_B'], 1)
        self.assertEqual(model.metriques['classes_avec_un_seul_exemple'], ['CAUSE_TRAIN_B'])
        self.assertFalse(model.metriques['jeu_validation_disponible'])
