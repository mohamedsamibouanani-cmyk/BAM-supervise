from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from openpyxl import Workbook

from supervision.models import FichierImport, Systeme, Superviseur, ValeurAttributSnapshot
from supervision.services.importer import ImportValidationError, import_excel
from supervision.services.security import decrypt_file_bytes, decrypt_sensitive


class SecureImportTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.tmp.name,
            BAM_DATA_ENCRYPTION_KEY='secure-import-test-key',
        )
        self.settings_override.enable()
        self.user = Superviseur.objects.create_user(username='sup', password='Strong-Test-Password-2026!')
        self.system = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)

    def tearDown(self):
        self.settings_override.disable()
        self.tmp.cleanup()

    def _xlsx(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['CODE_ENVOI', 'ARTICLE', 'ORG_COMMERCIALE', 'TELEPHONE'])
        sheet.append(['E001', 'SRV01', '2000', '+212600000000'])
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def test_import_source_and_sensitive_attribute_are_protected_at_rest(self):
        raw = self._xlsx()
        upload = SimpleUploadedFile(
            'SMI.xlsx', raw,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        imported = import_excel(upload, self.system, self.user)

        stored = Path(imported.chemin_stockage).read_bytes()
        self.assertTrue(stored.startswith(b'bamfile:v1:'))
        self.assertEqual(decrypt_file_bytes(stored), raw)

        value = ValeurAttributSnapshot.objects.select_related('attribut').get(attribut__code_attribut='TELEPHONE')
        self.assertTrue(value.attribut.sensible)
        self.assertTrue(value.valeur_brute.startswith('enc:v1:'))
        self.assertEqual(decrypt_sensitive(value.valeur_brute), '+212600000000')
        self.assertTrue(value.valeur_normalisee.startswith('hmac:v1:'))
        self.assertNotIn('+212600000000', value.valeur_normalisee)

    def test_invalid_workbook_is_encrypted_and_failure_is_audited(self):
        raw = b'not-an-excel-workbook-sensitive-payload'
        upload = SimpleUploadedFile('broken.xlsx', raw, content_type='application/octet-stream')
        with self.assertRaises(ImportValidationError):
            import_excel(upload, self.system, self.user)

        record = FichierImport.objects.get(systeme=self.system)
        self.assertEqual(record.statut_import, FichierImport.Statut.ECHEC)
        self.assertTrue(record.message_erreur)
        stored = Path(record.chemin_stockage).read_bytes()
        self.assertTrue(stored.startswith(b'bamfile:v1:'))
        self.assertEqual(decrypt_file_bytes(stored), raw)

    def test_legacy_plaintext_import_file_is_upgraded_to_encrypted_storage(self):
        raw = b'legacy-plaintext-excel-payload'
        legacy_path = Path(self.tmp.name) / 'legacy.xlsx'
        legacy_path.write_bytes(raw)
        record = FichierImport.objects.create(
            systeme=self.system,
            superviseur=self.user,
            nom_fichier='legacy.xlsx',
            chemin_stockage=str(legacy_path),
            checksum_sha256='f' * 64,
            statut_import=FichierImport.Statut.CHARGE,
        )

        call_command('protect_sensitive_data')
        record.refresh_from_db()
        upgraded_path = Path(record.chemin_stockage)
        self.assertEqual(upgraded_path.suffix, '.enc')
        self.assertFalse(legacy_path.exists())
        encrypted = upgraded_path.read_bytes()
        self.assertTrue(encrypted.startswith(b'bamfile:v1:'))
        self.assertEqual(decrypt_file_bytes(encrypted), raw)
