from django.core.management import call_command
from django.test import TestCase

from supervision.forms import ValidationMotifForm
from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    EnvoiSnapshot, FichierImport, ServiceSnapshot, Systeme, Superviseur,
    ValeurAttributSnapshot,
)
from supervision.services.comparison import run_campaign
from supervision.services.utils import stable_hash


class PhoneDifferenceMultiSystemTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(
            username='phone-difference', password='Strong-Test-Password-2026!'
        )
        self.systems = {
            system.code_systeme: system
            for system in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])
        }

    def _anomaly(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        attribute = AttributDefinition.objects.get(code_attribut='TELEPHONE_NOTIFICATION')
        values = {
            'SMI': '0611111111',
            'SICOM': '0611111111',
            'SIBO': '0699999999',
        }
        for code in ('SMI', 'SICOM', 'SIBO'):
            file_import = FichierImport.objects.create(
                systeme=self.systems[code],
                superviseur=self.user,
                nom_fichier=f'PHONE_DIFF_{code}.xlsx',
                chemin_stockage=f'/tmp/PHONE_DIFF_{code}.xlsx',
                checksum_sha256=stable_hash('phone-difference', code),
                statut_import='CHARGE',
                nb_lignes_source=1,
                nb_lignes_retenues=1,
            )
            CampagneImport.objects.create(
                campagne=campaign,
                systeme=self.systems[code],
                fichier_import=file_import,
            )
            shipment = EnvoiSnapshot.objects.create(
                fichier_import=file_import,
                code_envoi='PHONE-DIFF-001',
                num_commande='CMD-PHONE-DIFF-001',
                org_commerciale='2000',
                ligne_premiere=2,
                empreinte_envoi=stable_hash('PHONE-DIFF-001', code),
            )
            service = ServiceSnapshot.objects.create(
                envoi_snapshot=shipment,
                code_service='30100',
                libelle_service='Service colis',
                ligne_source=2,
                empreinte_service=stable_hash('PHONE-DIFF-001', code, '30100'),
            )
            ValeurAttributSnapshot.objects.create(
                service_snapshot=service,
                attribut=attribute,
                valeur_brute=values[code],
                valeur_normalisee=values[code],
                est_vide=False,
                format_source_conforme=True,
            )

        run_campaign(campaign)
        return campaign.anomalies.get(
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.DIFFERENT,
            attribut__code_attribut='TELEPHONE_NOTIFICATION',
        )

    def _form(self, anomaly, targets):
        prediction = anomaly.predictions.get(
            explication__role_diagnostic='CONSTAT_ATTRIBUT_DIFFERENT'
        )
        return ValidationMotifForm(anomaly, data={
            'prediction': prediction.pk,
            'predictions': [prediction.pk],
            'multi_cause_mode': '1',
            'motif_final': prediction.motif_id,
            'nouveau_motif': '',
            'decision': 'MODIFIE',
            'commentaire': '',
            'systeme_a_corriger_final': [self.systems[code].pk for code in targets],
        })

    def test_phone_difference_accepts_one_system(self):
        anomaly = self._anomaly()
        form = self._form(anomaly, ['SIBO'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            [system.code_systeme for system in form.cleaned_data['systemes_a_corriger_finaux']],
            ['SIBO'],
        )

    def test_phone_difference_accepts_two_systems(self):
        anomaly = self._anomaly()
        form = self._form(anomaly, ['SMI', 'SICOM'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            {system.code_systeme for system in form.cleaned_data['systemes_a_corriger_finaux']},
            {'SMI', 'SICOM'},
        )

    def test_phone_difference_accepts_all_three_systems(self):
        anomaly = self._anomaly()
        form = self._form(anomaly, ['SMI', 'SICOM', 'SIBO'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            {system.code_systeme for system in form.cleaned_data['systemes_a_corriger_finaux']},
            {'SMI', 'SICOM', 'SIBO'},
        )
