from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from supervision.models import CampagneSupervision, FichierImport, Systeme, Superviseur
from supervision.services.utils import sha256_bytes


class CampaignStatusTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='superviseur', password='Strong-Test-Password-2026!')
        self.systems = {
            code: Systeme.objects.create(code_systeme=code, nom_systeme=code, ordre_comparaison=index)
            for index, code in enumerate(('SMI', 'SICOM', 'SIBO'), 1)
        }
        self.client.force_login(self.user)

    @staticmethod
    def _xlsx_bytes(code):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['CODE_ENVOI', 'ARTICLE', 'ORG_COMMERCIALE'])
        sheet.append([f'E-{code}', 'SRV01', '2000'])
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    @staticmethod
    def _upload(name, payload):
        return SimpleUploadedFile(
            name,
            payload,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_duplicate_import_creates_a_failed_campaign_not_a_prepared_one(self):
        smi_payload = self._xlsx_bytes('SMI')
        FichierImport.objects.create(
            systeme=self.systems['SMI'],
            superviseur=self.user,
            nom_fichier='SMI.xlsx',
            chemin_stockage='/tmp/SMI.xlsx.enc',
            checksum_sha256=sha256_bytes(smi_payload),
            statut_import=FichierImport.Statut.CHARGE,
        )

        response = self.client.post(reverse('campaign_create'), {
            'fichier_smi': self._upload('SMI.xlsx', smi_payload),
            'fichier_sicom': self._upload('SICOM.xlsx', self._xlsx_bytes('SICOM')),
            'fichier_sibo': self._upload('SIBO.xlsx', self._xlsx_bytes('SIBO')),
        })

        self.assertEqual(response.status_code, 200)
        campaign = CampagneSupervision.objects.latest('pk')
        self.assertEqual(campaign.statut, CampagneSupervision.Statut.ECHEC)
        self.assertEqual(campaign.imports.count(), 0)
        self.assertIn('déjà été importé', campaign.message_erreur)
        self.assertContains(response, 'Import impossible')

    def test_campaign_history_prioritizes_operational_information(self):
        CampagneSupervision.objects.create(
            superviseur=self.user,
            statut=CampagneSupervision.Statut.ECHEC,
            message_erreur='Ce fichier SMI a déjà été importé.',
        )

        response = self.client.get(reverse('campaign_list'))

        self.assertContains(response, 'Échec')
        self.assertContains(response, 'Ce fichier SMI a déjà été importé.')
        self.assertContains(response, 'Comparaison non exécutée')
        self.assertNotContains(response, 'Version moteur')
