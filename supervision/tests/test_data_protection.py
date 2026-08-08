from django.test import SimpleTestCase, override_settings

from supervision.services.security import decrypt_sensitive, encrypt_sensitive, sensitive_fingerprint


@override_settings(BAM_DATA_ENCRYPTION_KEY='unit-test-data-protection-key')
class SensitiveDataProtectionTests(SimpleTestCase):
    def test_sensitive_value_is_encrypted_and_roundtrips(self):
        plaintext = '+212600000000'
        encrypted = encrypt_sensitive(plaintext)
        self.assertTrue(encrypted.startswith('enc:v1:'))
        self.assertNotIn(plaintext, encrypted)
        self.assertEqual(decrypt_sensitive(encrypted), plaintext)

    def test_sensitive_fingerprint_is_keyed_and_deterministic(self):
        plaintext = '+212600000000'
        fp1 = sensitive_fingerprint(plaintext)
        fp2 = sensitive_fingerprint(plaintext)
        self.assertTrue(fp1.startswith('hmac:v1:'))
        self.assertEqual(fp1, fp2)
        self.assertNotIn(plaintext, fp1)

    def test_different_values_have_different_fingerprints(self):
        self.assertNotEqual(
            sensitive_fingerprint('+212600000000'),
            sensitive_fingerprint('+212611111111'),
        )
