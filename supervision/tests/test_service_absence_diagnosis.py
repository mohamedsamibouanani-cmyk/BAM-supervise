from django.core.management import call_command
from django.test import TestCase

from supervision.models import (
    AttributDefinition, CampagneImport, CampagneSupervision, EnvoiSnapshot,
    FichierImport, ServiceSnapshot, Systeme, Superviseur, ValeurAttributSnapshot,
)
from supervision.services.comparison import run_campaign
from supervision.services.utils import stable_hash


class ServiceAbsenceDiagnosisTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(username='superviseur', password='secret12345')
        self.systems = {system.code_systeme: system for system in Systeme.objects.all()}

    def _file(self, code):
        return FichierImport.objects.create(
            systeme=self.systems[code],
            superviseur=self.user,
            nom_fichier=f'{code}.xlsx',
            chemin_stockage=f'/tmp/{code}.xlsx',
            checksum_sha256=stable_hash('service-absence', code),
            statut_import='CHARGE',
            nb_lignes_source=1,
            nb_lignes_retenues=1,
        )

    def _campaign_with_missing_sicom_service(self, *, invalid_source_city=False):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {code: self._file(code) for code in self.systems}
        shipments = {}

        for code, file_import in files.items():
            CampagneImport.objects.create(
                campagne=campaign,
                systeme=self.systems[code],
                fichier_import=file_import,
            )
            shipments[code] = EnvoiSnapshot.objects.create(
                fichier_import=file_import,
                code_envoi='LD875872621MA',
                num_commande='CMD-001',
                org_commerciale='2000',
                ligne_premiere=2,
                empreinte_envoi=stable_hash(code, 'LD875872621MA'),
            )

        for code in ('SMI', 'SIBO'):
            ServiceSnapshot.objects.create(
                envoi_snapshot=shipments[code],
                code_service='30801',
                libelle_service='Service test',
                ligne_source=2,
                empreinte_service=stable_hash(code, 'LD875872621MA', '30801'),
            )

        if invalid_source_city:
            city = AttributDefinition.objects.get(code_attribut='VILLE')
            for code in ('SMI', 'SIBO'):
                ValeurAttributSnapshot.objects.create(
                    envoi_snapshot=shipments[code],
                    attribut=city,
                    valeur_brute='???',
                    valeur_normalisee='???',
                    est_vide=False,
                    format_source_conforme=False,
                )

        run_campaign(campaign)
        return campaign

    def test_missing_service_is_identified_in_the_only_missing_system(self):
        campaign = self._campaign_with_missing_sicom_service()

        anomaly = campaign.anomalies.get(niveau='SERVICE', type_ecart='ABSENT', code_service='30801')
        self.assertEqual(anomaly.systeme_ecart.code_systeme, 'SICOM')

        prediction = anomaly.predictions.get()
        self.assertEqual(prediction.motif.code_motif, 'SERVICE_ABSENT')
        self.assertEqual(prediction.motif.libelle, 'Service absent / non synchronisé')
        self.assertEqual(prediction.systeme_a_corriger_predit.code_systeme, 'SICOM')
        self.assertEqual(prediction.explication['systemes_a_corriger'], ['SICOM'])
        self.assertIn('SMI', prediction.explication['message'])
        self.assertIn('SIBO', prediction.explication['message'])
        self.assertIn('SICOM', prediction.explication['message'])
        self.assertFalse(anomaly.predictions.filter(motif__code_motif='MOTIF_INCONNU').exists())

    def test_source_cause_keeps_priority_over_missing_system_fallback(self):
        campaign = self._campaign_with_missing_sicom_service(invalid_source_city=True)

        anomaly = campaign.anomalies.get(niveau='SERVICE', type_ecart='ABSENT', code_service='30801')
        predictions = list(anomaly.predictions.select_related('motif').all())

        self.assertTrue(predictions)
        self.assertFalse(any(p.motif.code_motif == 'SERVICE_ABSENT' for p in predictions))
        targets = set()
        for prediction in predictions:
            targets.update(prediction.explication.get('systemes_a_corriger') or [])
        self.assertEqual(targets, {'SMI', 'SIBO'})
        self.assertNotIn('SICOM', targets)
