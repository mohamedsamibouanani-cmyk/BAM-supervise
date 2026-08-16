import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from supervision.forms_anomaly import MultiCauseValidationForm
from supervision.models import (
    Anomalie, CampagneSupervision, ExempleApprentissage, ModeleML, Motif,
    PredictionMotif, Systeme, Superviseur, ValidationMotif,
)
from supervision.services.ml_inference import ensure_ml_predictions
from supervision.services.ml_training import training_status


class MLActivationAfterSixExamplesTests(TestCase):
    """Trois vérifications du scénario d'activation après six causes utiles.

    Le modèle démarre à partir de 6 exemples causaux exploitables, avec au moins
    deux causes différentes et au moins deux validations humaines par cause.
    """

    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-six-examples', password='Strong-Test-Password-2026!'
        )
        self.system = Systeme.objects.create(
            code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.motifs = [
            Motif.objects.create(
                code_motif=f'ML_DEMO_{index}',
                libelle=f'Motif démonstration {index}',
                niveau_applicable=Anomalie.Niveau.ENVOI,
                categorie='APPRENTISSAGE',
                actif=True,
            )
            for index in range(1, 4)
        ]

        # Trois causes réelles confirmées deux fois chacune : 2 / 2 / 2.
        labels = [
            self.motifs[0], self.motifs[0],
            self.motifs[1], self.motifs[1],
            self.motifs[2], self.motifs[2],
        ]
        for index, motif in enumerate(labels, start=1):
            anomaly = Anomalie.objects.create(
                campagne=self.campaign,
                niveau=Anomalie.Niveau.ENVOI,
                type_ecart=Anomalie.TypeEcart.ABSENT,
                code_envoi=f'ML-HIST-{index:03d}',
                systeme_ecart=self.system,
                empreinte_anomalie=f'{index:064x}',
            )
            validation = ValidationMotif.objects.create(
                anomalie=anomaly,
                motif_final=motif,
                systeme_a_corriger_final=self.system,
                superviseur=self.user,
                decision=ValidationMotif.Decision.MODIFIE,
                version_validation=1,
                est_finale=True,
            )
            ExempleApprentissage.objects.create(
                validation=validation,
                motif_label=motif,
                systeme_a_corriger_label=self.system,
                caracteristiques={
                    'niveau': Anomalie.Niveau.ENVOI,
                    'type_ecart': Anomalie.TypeEcart.ABSENT,
                    'service': f'SVC-{motif.code_motif}',
                    'attribut': '',
                    'systeme_ecart': 'SICOM',
                },
                eligible=True,
            )

    def _target_anomaly(self):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='ML-TARGET-001',
            systeme_ecart=self.system,
            empreinte_anomalie='f' * 64,
        )

    def test_iteration_1_six_examples_and_multiple_motifs_are_ready(self):
        status = training_status()

        self.assertEqual(status['eligible_examples'], 6)
        self.assertEqual(status['trainable_examples'], 6)
        self.assertEqual(status['motif_classes'], 3)
        self.assertEqual(status['trainable_classes'], 3)
        self.assertTrue(status['ready'])

    def test_iteration_2_opening_an_anomaly_trains_and_predicts_on_demand(self):
        anomaly = self._target_anomaly()

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=Path(tmpdir)):
                created = ensure_ml_predictions(anomaly)

                model = ModeleML.objects.get(actif=True)
                self.assertEqual(model.nb_exemples, 6)
                self.assertEqual(model.metriques['nb_classes'], 3)
                self.assertTrue(Path(model.chemin_fichier).exists())

        self.assertGreaterEqual(len(created), 1)
        self.assertLessEqual(len(created), 2)
        self.assertTrue(all(item.source_prediction == PredictionMotif.Source.ML for item in created))
        self.assertTrue(all(0 <= float(item.score_confiance) <= 1 for item in created))

    def test_iteration_3_ml_suggestion_is_visible_with_probability(self):
        anomaly = self._target_anomaly()

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=Path(tmpdir)):
                created = ensure_ml_predictions(anomaly)
                form = MultiCauseValidationForm(anomaly)
                labels = [
                    form.fields['predictions'].label_from_instance(prediction)
                    for prediction in created
                ]

        self.assertTrue(labels)
        self.assertTrue(all('probabilité ML' in label for label in labels))
        self.assertTrue(all('%' in label for label in labels))
