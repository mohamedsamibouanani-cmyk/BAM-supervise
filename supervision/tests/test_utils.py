from django.test import SimpleTestCase
from supervision.services.utils import clean_text, normalize_value, stable_hash


class UtilsTests(SimpleTestCase):
    def test_invalid_excel_marker_is_empty(self):
        self.assertEqual(clean_text('_x001A__x001A__x001A_'), '')

    def test_numeric_normalization(self):
        self.assertEqual(normalize_value('1 000,50', 'NOMBRE'), '1000.50')

    def test_hash_is_stable(self):
        self.assertEqual(stable_hash('A', 'B'), stable_hash('A', 'B'))
