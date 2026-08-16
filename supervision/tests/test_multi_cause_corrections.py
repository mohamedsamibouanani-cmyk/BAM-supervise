from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from supervision.models import (
    Anomalie, CampagneSupervision, GroupeResponsable, Motif, PredictionMotif,
    Systeme, Superviseur,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
)
class MultiCauseCorrectionTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='multi-cause', password='secret12345')
        self.client.force_login(self.user)
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        GroupeResponsable.objects.create(
            systeme=self.smi, nom_groupe='Equipe SMI', email_collectif='smi@example.com'
        )
        GroupeResponsable.objects.create(
            systeme=self.sicom, nom_groupe='Equipe SICOM', email_collectif='sicom@example.com'
        )
        self.campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=self.campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='E-MULTI-CAUSE',
            systeme_ecart=self.sibo,
            empreinte_anomalie='m' * 64,
            statut=Anomalie.Statut.ANALYSEE,
        )
        self.ville = Motif.objects.create(
            code_motif='VILLE_MANQUANTE',
            libelle='Ville obligatoire manquante',
            niveau_applicable='ENVOI',
            categorie='DONNEE',
            champ_typique='VILLE',
        )
        self.telephone = Motif.objects.create(
            code_motif='TELEPHONE_MANQUANT',
            libelle='Téléphone obligatoire manquant',
            niveau_applicable='ENVOI',
            categorie='DONNEE',
            champ_typique='TELEPHONE',
        )
        self.pred_ville = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.REGLE,
            motif=self.ville,
            systeme_a_corriger_predit=self.smi,
            rang=1,
            score_confiance=Decimal('0.9500'),
            explication={
                'systemes_a_corriger': ['SMI', 'SICOM'],
                'indices': [
                    {'systeme': 'SMI', 'champ': 'VILLE', 'constat': 'Champ vide ou absent dans la source'},
                    {'systeme': 'SICOM', 'champ': 'VILLE', 'constat': 'Champ vide ou absent dans la source'},
                ],
            },
        )
        self.pred_tel = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.REGLE,
            motif=self.telephone,
            systeme_a_corriger_predit=self.smi,
            rang=2,
            score_confiance=Decimal('0.9000'),
            explication={
                'systemes_a_corriger': ['SMI'],
                'indices': [
                    {'systeme': 'SMI', 'champ': 'TELEPHONE', 'constat': 'Champ vide ou absent dans la source'},
                ],
            },
        )

    def test_selected_causes_are_validated_together_and_filtered_per_system_email(self):
        response = self.client.post(reverse('anomaly_validate', args=[self.anomaly.pk]), {
            'predictions': [self.pred_ville.pk, self.pred_tel.pk],
            'prediction': self.pred_ville.pk,
            'multi_cause_mode': '1',
            'decision': 'ACCEPTE',
            'systeme_a_corriger_final': self.smi.pk,
            'commentaire': '',
        })

        self.assertRedirects(response, reverse('anomaly_detail', args=[self.anomaly.pk]))
        final_validations = self.anomaly.validations.filter(est_finale=True).order_by('version_validation')
        self.assertEqual(final_validations.count(), 2)
        self.assertEqual(
            list(final_validations.values_list('motif_final__code_motif', flat=True)),
            ['VILLE_MANQUANTE', 'TELEPHONE_MANQUANT'],
        )

        self.assertEqual(len(mail.outbox), 2)
        smi_email = next(message for message in mail.outbox if '[SMI]' in message.subject)
        sicom_email = next(message for message in mail.outbox if '[SICOM]' in message.subject)

        self.assertIn('Ville obligatoire manquante', smi_email.body)
        self.assertIn('Téléphone obligatoire manquant', smi_email.body)
        self.assertIn('champ : VILLE', smi_email.body)
        self.assertIn('champ : TELEPHONE', smi_email.body)

        self.assertIn('Ville obligatoire manquante', sicom_email.body)
        self.assertIn('champ : VILLE', sicom_email.body)
        self.assertNotIn('Téléphone obligatoire manquant', sicom_email.body)
        self.assertNotIn('champ : TELEPHONE', sicom_email.body)

        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.statut, Anomalie.Statut.NOTIFIEE)

    def test_supervisor_can_validate_more_than_three_simultaneous_causes(self):
        extra_predictions = []
        for index in (3, 4):
            motif = Motif.objects.create(
                code_motif=f'CAUSE_{index}',
                libelle=f'Cause métier {index}',
                niveau_applicable='ENVOI',
                categorie='DONNEE',
                champ_typique=f'CHAMP_{index}',
            )
            extra_predictions.append(PredictionMotif.objects.create(
                anomalie=self.anomaly,
                source_prediction=PredictionMotif.Source.REGLE,
                motif=motif,
                systeme_a_corriger_predit=self.smi,
                rang=index,
                score_confiance=Decimal('0.8000'),
                explication={
                    'systemes_a_corriger': ['SMI'],
                    'indices': [
                        {
                            'systeme': 'SMI',
                            'champ': f'CHAMP_{index}',
                            'constat': 'Champ vide ou absent dans la source',
                        }
                    ],
                },
            ))

        all_predictions = [self.pred_ville, self.pred_tel, *extra_predictions]
        response = self.client.post(reverse('anomaly_validate', args=[self.anomaly.pk]), {
            'predictions': [prediction.pk for prediction in all_predictions],
            'prediction': self.pred_ville.pk,
            'multi_cause_mode': '1',
            'decision': 'ACCEPTE',
            'systeme_a_corriger_final': self.smi.pk,
            'commentaire': '',
        })

        self.assertRedirects(response, reverse('anomaly_detail', args=[self.anomaly.pk]))
        self.assertEqual(self.anomaly.validations.filter(est_finale=True).count(), 4)
