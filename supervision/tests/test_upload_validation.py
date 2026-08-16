from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from openpyxl import Workbook

from supervision.forms import CampaignImportForm, MAX_UPLOAD_BYTES


class CampaignUploadValidationTests(SimpleTestCase):
    def _xlsx_bytes(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['CODE_ENVOI', 'ARTICLE', 'ORG_COMMERCIALE'])
        sheet.append(['E001', 'SRV01', '2000'])
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def _valid_upload(self, name):
        return SimpleUploadedFile(
            name,
            self._xlsx_bytes(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_valid_xlsx_files_are_accepted(self):
        form = CampaignImportForm(files={
            'fichier_smi': self._valid_upload('SMI.xlsx'),
            'fichier_sicom': self._valid_upload('SICOM.xlsx'),
            'fichier_sibo': self._valid_upload('SIBO.xlsx'),
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_disguised_non_excel_payload_is_rejected(self):
        form = CampaignImportForm(files={
            'fichier_smi': SimpleUploadedFile('SMI.xlsx', b'not-an-excel-file'),
            'fichier_sicom': self._valid_upload('SICOM.xlsx'),
            'fichier_sibo': self._valid_upload('SIBO.xlsx'),
        })
        self.assertFalse(form.is_valid())
        self.assertIn('fichier_smi', form.errors)
        self.assertIn('contenu du fichier', form.errors['fichier_smi'][0].lower())

    def test_oversized_file_is_rejected_before_parsing(self):
        oversized = self._valid_upload('SMI.xlsx')
        oversized.size = MAX_UPLOAD_BYTES + 1
        form = CampaignImportForm(files={
            'fichier_smi': oversized,
            'fichier_sicom': self._valid_upload('SICOM.xlsx'),
            'fichier_sibo': self._valid_upload('SIBO.xlsx'),
        })
        self.assertFalse(form.is_valid())
        self.assertIn('fichier_smi', form.errors)
        self.assertIn('20 mo', form.errors['fichier_smi'][0].lower())
