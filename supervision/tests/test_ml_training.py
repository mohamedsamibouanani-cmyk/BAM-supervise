import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone

from supervision.models import (
    Anomalie,
    CampagneSupervision,
    ExempleApprentissage,
    ModeleML,
    Motif,
    Systeme,
    Superviseur,
    ValidationMotif,
)
from supervision.services.ml_training import retrain_after_campaign_if_ready


class MLTrainingTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='ml-supervisor',
            password='Strong-Test-Password-2026!',
            nom_complet='ML Supervisor',
        )
        self.system = Systeme.objects.create(
            code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3
        )
        self.motif_a = Motif.objects.create(
            code_motif='MOTIF_A',
            libelle='Motif A',
            niveau_applicable='ATTRIBUT',
            categorie='TEST',
        )
        self.motif_b = Motif.objects.create(
            code_motif='MOTIF_B',
            libelle='Motif B',
            niveau_applicable='ATTRIBUT',
            categorie='TEST',
        )
        self.campaign = CampagneSupervision.objects.create(
            superviseur=self.user,
            statut=CampagneSupervision.Statut.TERMINEE,
            terminee_le=timezone.now(),
        )

    def _add_example(self, index, motif):
        anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.ABSENT if index % 2 else Anomalie.TypeEcart.DIFFERENT,
            code_envoi=f'ML{index:03d}',
            systeme_ecart=self.system,
            empreinte_anomalie=f'{index:064x}',
            statut=Anomalie.Statut.VALIDEE,
        )
        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            motif_final=motif,
            systeme_a_corriger_final=self.system,
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
            est_finale=True,
        )
        ExempleApprentissage.objects.create(
            validation=validation,
            motif_label=motif,
            systeme_a_corriger_label=self.system,
            caracteristiques={
                'niveau': anomaly.niveau,
                'type_ecart': anomaly.type_ecart,
                'service': '',
                'attribut': '',
                'systeme_ecart': self.system.code_systeme,
            },
            eligible=True,
        )

    def test_retrain_creates_and_activates_model_after_reviewed_campaign(self):
        for index in range(1, 7):
            self._add_example(index, self.motif_a if index <= 3 else self.motif_b)

        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=Path(tmpdir)):
                result = retrain_after_campaign_if_ready(self.campaign)

                self.assertTrue(result['trained'])
                model = ModeleML.objects.get(actif=True)
                self.assertEqual(model.nb_exemples, 6)
                self.assertEqual(model.metriques['campagne_declencheuse_id'], self.campaign.pk)
                self.assertTrue(Path(model.chemin_fichier).exists())
                self.assertTrue(model.chemin_fichier.endswith('.joblib'))

    def test_retrain_waits_until_all_anomalies_are_validated(self):
        for index in range(1, 6):
            self._add_example(index, self.motif_a if index <= 3 else self.motif_b)
        Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='ML-PENDING',
            systeme_ecart=self.system,
            empreinte_anomalie='f' * 64,
            statut=Anomalie.Statut.ANALYSEE,
        )

        result = retrain_after_campaign_if_ready(self.campaign, min_examples=4)
        self.assertFalse(result['trained'])
        self.assertEqual(result['reason'], 'campagne_non_entierement_validee')
        self.assertFalse(ModeleML.objects.exists())
