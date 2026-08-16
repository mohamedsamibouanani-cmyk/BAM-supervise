import tempfile
from decimal import Decimal
from pathlib import Path

from django.test import TestCase, override_settings

from supervision.forms_anomaly import MultiCauseValidationForm
from supervision.models import (
    Anomalie, CampagneSupervision, ExempleApprentissage, ModeleML, Motif,
    PredictionMotif, RegleMetier, Systeme, Superviseur, ValidationMotif,
)
from supervision.services.ml_training import maybe_retrain_model


class PredictionProbabilityDisplayTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-display', password='Strong-Test-Password-2026!'
        )
        self.system = Systeme.objects.create(
            code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='ML-DISPLAY-001',
            systeme_ecart=self.system,
            empreinte_anomalie='m' * 64,
        )
        self.motif = Motif.objects.create(
            code_motif='VILLE_TEST', libelle='Ville manquante',
            niveau_applicable='ENVOI', categorie='TEST'
        )

    def test_rule_label_has_no_percentage(self):
        rule = RegleMetier.objects.create(
            code_regle='TEST_RULE_NO_PERCENT',
            motif_suggere=self.motif,
            niveau_anomalie='ENVOI',
            type_controle='AUTRE',
            expression_regle={},
            seuil_confiance=Decimal('0.9500'),
            priorite=1,
            actif=True,
        )
        prediction = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.REGLE,
            regle=rule,
            motif=self.motif,
            systeme_a_corriger_predit=self.system,
            rang=1,
            score_confiance=Decimal('0.9500'),
            explication={'message': 'Ville manquante'},
        )
        form = MultiCauseValidationForm(self.anomaly)
        label = form.fields['predictions'].label_from_instance(prediction)
        self.assertEqual(label, 'Ville manquante')
        self.assertNotIn('%', label)

    def test_learned_case_label_has_no_percentage(self):
        prediction = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.APPRENTISSAGE,
            motif=self.motif,
            systeme_a_corriger_predit=self.system,
            rang=1,
            score_confiance=Decimal('0.7500'),
            explication={'message': 'Ville manquante'},
        )
        form = MultiCauseValidationForm(self.anomaly)
        label = form.fields['predictions'].label_from_instance(prediction)
        self.assertIn('cas similaire validé', label)
        self.assertNotIn('%', label)

    def test_ml_label_displays_probability(self):
        model = ModeleML.objects.create(
            nom_modele='Test ML', algorithme='Test', version_modele='test-v1',
            entraine_le=self.campaign.demarree_le, nb_exemples=20,
            metriques={}, chemin_fichier='/tmp/test.joblib',
            checksum_sha256='a' * 64, actif=True,
        )
        prediction = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.ML,
            modele=model,
            motif=self.motif,
            systeme_a_corriger_predit=self.system,
            rang=1,
            score_confiance=Decimal('0.8234'),
            explication={'message': 'Ville manquante'},
        )
        form = MultiCauseValidationForm(self.anomaly)
        label = form.fields['predictions'].label_from_instance(prediction)
        self.assertIn('probabilité ML 82%', label)


class AutomaticMLTrainingTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-training', password='Strong-Test-Password-2026!'
        )
        self.system = Systeme.objects.create(
            code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.motif_a = Motif.objects.create(
            code_motif='ML_A', libelle='Motif A', niveau_applicable='ENVOI', categorie='TEST'
        )
        self.motif_b = Motif.objects.create(
            code_motif='ML_B', libelle='Motif B', niveau_applicable='ENVOI', categorie='TEST'
        )

    def _example(self, index, motif):
        anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi=f'ML-{index:03d}',
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
        return ExempleApprentissage.objects.create(
            validation=validation,
            motif_label=motif,
            systeme_a_corriger_label=self.system,
            caracteristiques={
                'niveau': 'ENVOI',
                'type_ecart': 'ABSENT',
                'service': 'SVC-A' if motif == self.motif_a else 'SVC-B',
                'attribut': 'VILLE' if motif == self.motif_a else 'TELEPHONE',
                'systeme_ecart': 'SMI',
            },
            eligible=True,
        )

    def test_training_creates_active_model_when_history_is_sufficient(self):
        # Le signal ne s'exécute qu'après commit ; TestCase garde une transaction
        # ouverte. On appelle donc explicitement le service pour tester le moteur.
        for index in range(20):
            self._example(index + 1, self.motif_a if index % 2 == 0 else self.motif_b)

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=Path(tmpdir)):
                model = maybe_retrain_model()

        self.assertIsNotNone(model)
        self.assertTrue(model.actif)
        self.assertEqual(model.nb_exemples, 20)
        self.assertEqual(model.metriques['nb_classes'], 2)
        self.assertTrue(ModeleML.objects.filter(pk=model.pk, actif=True).exists())
