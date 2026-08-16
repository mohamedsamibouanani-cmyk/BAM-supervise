from django.test import TestCase

from supervision.models import (
    Anomalie, AttributDefinition, CampagneSupervision, ExempleApprentissage,
    Motif, Systeme, Superviseur, ValidationMotif,
)
from supervision.services.learning_capture import (
    apply_learning_cause,
    available_learning_causes,
    resolve_learning_cause,
)
from supervision.services.ml_training import training_status


class LearningCauseCaptureTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='learning-cause', password='Strong-Test-Password-2026!'
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
        self.technical = Motif.objects.create(
            code_motif='ATTRIBUT_NON_SYNCHRONISE',
            libelle='Valeur absente', niveau_applicable='ATTRIBUT',
            categorie='SYNCHRONISATION', actif=True,
        )
        self.causal = Motif.objects.create(
            code_motif='ATTRIBUT_MODIFICATION_NON_PROPAGEE',
            libelle='Modification non propagée', niveau_applicable='ATTRIBUT',
            categorie='APPRENTISSAGE', actif=True,
        )

    def _anomaly(self, code='CAPTURE-001', difference=False):
        return Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=(
                Anomalie.TypeEcart.DIFFERENT if difference else Anomalie.TypeEcart.ABSENT
            ),
            code_envoi=code,
            code_service='30100',
            attribut=self.attribute,
            systeme_ecart=None if difference else self.system,
            empreinte_anomalie=(code.replace('-', '') + '0' * 64)[:64],
        )

    def _validation(self, anomaly, version=1):
        return ValidationMotif.objects.create(
            anomalie=anomaly,
            motif_final=self.technical,
            systeme_a_corriger_final=self.system,
            superviseur=self.user,
            decision=ValidationMotif.Decision.MODIFIE,
            version_validation=version,
            est_finale=True,
        )

    def test_iteration_1_constat_technique_est_exclu_des_causes_proposees(self):
        anomaly = self._anomaly()
        codes = set(available_learning_causes(anomaly).values_list('code_motif', flat=True))

        self.assertNotIn('ATTRIBUT_NON_SYNCHRONISE', codes)
        self.assertIn('ATTRIBUT_MODIFICATION_NON_PROPAGEE', codes)

    def test_iteration_2_cause_confirmee_remplace_le_label_technique_de_l_exemple(self):
        anomaly = self._anomaly('CAPTURE-002')
        validation = self._validation(anomaly)
        ExempleApprentissage.objects.create(
            validation=validation,
            motif_label=self.technical,
            systeme_a_corriger_label=self.system,
            caracteristiques={'niveau': 'ATTRIBUT'},
            eligible=True,
        )

        cause = resolve_learning_cause(anomaly, cause_id=self.causal.pk)
        example = apply_learning_cause(validation, cause)

        self.assertEqual(example.motif_label, self.causal)
        self.assertTrue(example.eligible)
        self.assertEqual(example.caracteristiques['attribut'], 'TELEPHONE_NOTIFICATION')
        self.assertIn('nb_valeurs_vides', example.caracteristiques)

    def test_iteration_3_deux_causes_confirmees_sont_comptees_sans_apprendre_le_constat(self):
        second_cause = Motif.objects.create(
            code_motif='ATTRIBUT_SOURCE_NON_RENSEIGNEE',
            libelle='Donnée non renseignée à la source',
            niveau_applicable='ATTRIBUT', categorie='APPRENTISSAGE', actif=True,
        )
        for index, cause in enumerate(
            [self.causal, self.causal, second_cause, second_cause], start=1
        ):
            anomaly = self._anomaly(f'CAPTURE-{index + 10:03d}')
            validation = self._validation(anomaly)
            apply_learning_cause(validation, cause)

        status = training_status()

        self.assertEqual(status['trainable_examples'], 4)
        self.assertEqual(status['trainable_classes'], 2)
        self.assertFalse(status['ready'])  # seuil global de 6 conservé
