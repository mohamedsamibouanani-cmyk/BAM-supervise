from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

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
        self.client.force_login(self.user)
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

    def test_missing_service_is_a_sync_constat_without_business_motif(self):
        campaign = self._campaign_with_missing_sicom_service()

        anomaly = campaign.anomalies.get(
            niveau='SERVICE', type_ecart='ABSENT', code_service='30801'
        )
        self.assertEqual(anomaly.systeme_ecart.code_systeme, 'SICOM')

        prediction = anomaly.predictions.get()
        # SERVICE_ABSENT is only an internal technical classification required by
        # the current routing/audit schema. It is explicitly hidden as a motif.
        self.assertEqual(prediction.motif.code_motif, 'SERVICE_ABSENT')
        self.assertEqual(prediction.systeme_a_corriger_predit.code_systeme, 'SICOM')
        self.assertEqual(prediction.explication['systemes_a_corriger'], ['SICOM'])
        self.assertEqual(prediction.explication['role_diagnostic'], 'CONSTAT_SERVICE')
        self.assertFalse(prediction.explication['afficher_motif'])
        self.assertEqual(prediction.explication['message'], 'Service 30801 absent dans SICOM.')
        self.assertFalse(anomaly.predictions.filter(motif__code_motif='MOTIF_INCONNU').exists())

        response = self.client.get(reverse('anomaly_detail', args=[anomaly.pk]))
        self.assertContains(response, 'Désynchronisation détectée')
        self.assertContains(response, 'Service 30801 non synchronisé')
        self.assertContains(response, 'Absent dans SICOM')
        self.assertNotContains(response, '98%')

    def test_service_level_does_not_invent_a_source_motif(self):
        campaign = self._campaign_with_missing_sicom_service(invalid_source_city=True)

        anomaly = campaign.anomalies.get(
            niveau='SERVICE', type_ecart='ABSENT', code_service='30801'
        )
        predictions = list(anomaly.predictions.select_related('motif').all())

        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0].explication['role_diagnostic'], 'CONSTAT_SERVICE')
        self.assertEqual(predictions[0].explication['systemes_a_corriger'], ['SICOM'])
        self.assertFalse(predictions[0].explication['afficher_motif'])
