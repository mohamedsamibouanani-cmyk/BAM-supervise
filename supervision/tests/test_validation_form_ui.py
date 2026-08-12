from django.test import TestCase
from django.urls import reverse

from supervision.forms import ValidationMotifForm
from supervision.models import (
    Anomalie, CampagneSupervision, Motif, PredictionMotif, Systeme, Superviseur,
)


class ValidationFormUiTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='validation-ui', password='secret12345')
        self.client.force_login(self.user)
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        self.motif_ville = Motif.objects.create(
            code_motif='VILLE_MANQUANTE_UI',
            libelle='Ville obligatoire manquante',
            niveau_applicable='ENVOI',
            categorie='DONNEE',
        )
        self.motif_tel = Motif.objects.create(
            code_motif='TEL_MANQUANT_UI',
            libelle='Téléphone obligatoire manquant',
            niveau_applicable='ENVOI',
            categorie='DONNEE',
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau='ENVOI',
            type_ecart='ABSENT',
            code_envoi='E-VALIDATION-UI',
            systeme_ecart=self.sibo,
            empreinte_anomalie='v' * 64,
            statut=Anomalie.Statut.ANALYSEE,
        )
        self.prediction_ville = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.APPRENTISSAGE,
            motif=self.motif_ville,
            systeme_a_corriger_predit=self.smi,
            rang=1,
            score_confiance='0.9500',
            explication={'systemes_a_corriger': ['SMI', 'SICOM']},
        )
        self.prediction_tel = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.APPRENTISSAGE,
            motif=self.motif_tel,
            systeme_a_corriger_predit=self.sicom,
            rang=2,
            score_confiance='0.9000',
            explication={'systemes_a_corriger': ['SICOM']},
        )

    def test_acceptance_keeps_the_selected_prediction_even_with_stale_fields(self):
        form = ValidationMotifForm(self.anomaly, data={
            'prediction': self.prediction_tel.pk,
            'motif_final': self.motif_ville.pk,
            'systeme_a_corriger_final': self.smi.pk,
            'decision': 'ACCEPTE',
            'commentaire': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['motif_final'], self.motif_tel)
        self.assertEqual(form.cleaned_data['systeme_a_corriger_final'], self.sicom)

    def test_acceptance_without_prediction_uses_top_ranked_diagnostic(self):
        form = ValidationMotifForm(self.anomaly, data={
            'motif_final': self.motif_tel.pk,
            'systeme_a_corriger_final': self.sicom.pk,
            'decision': 'ACCEPTE',
            'commentaire': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['prediction'], self.prediction_ville)
        self.assertEqual(form.cleaned_data['motif_final'], self.motif_ville)
        self.assertEqual(form.cleaned_data['systeme_a_corriger_final'], self.smi)

    def test_legacy_acceptance_without_any_prediction_keeps_explicit_choice(self):
        legacy_anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau='ENVOI',
            type_ecart='ABSENT',
            code_envoi='E-LEGACY-VALIDATION',
            systeme_ecart=self.sibo,
            empreinte_anomalie='l' * 64,
            statut=Anomalie.Statut.ANALYSEE,
        )
        form = ValidationMotifForm(legacy_anomaly, data={
            'motif_final': self.motif_ville.pk,
            'systeme_a_corriger_final': self.smi.pk,
            'decision': 'ACCEPTE',
            'commentaire': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['prediction'])
        self.assertEqual(form.cleaned_data['motif_final'], self.motif_ville)
        self.assertEqual(form.cleaned_data['systeme_a_corriger_final'], self.smi)

    def test_validation_page_is_compact_and_decision_oriented(self):
        response = self.client.get(reverse('anomaly_validate', args=[self.anomaly.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Valider le diagnostic')
        self.assertContains(response, 'Causes détectées')
        self.assertContains(response, 'Décision')
        self.assertContains(response, 'Systèmes concernés')
        self.assertContains(response, 'Confirmer la sélection')
        self.assertContains(response, 'Ajuster le diagnostic')
        self.assertContains(response, 'À investiguer')
        self.assertContains(response, 'Ville obligatoire manquante · 95%')
        self.assertContains(response, 'Téléphone obligatoire manquant · 90%')
        self.assertContains(response, 'SMI')
        self.assertContains(response, 'SICOM')
        self.assertContains(response, 'validation-form.css')
        self.assertNotContains(response, 'Valider les corrections')
        self.assertNotContains(response, 'Corrections détectées')
