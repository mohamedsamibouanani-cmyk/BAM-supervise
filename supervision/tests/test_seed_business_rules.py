from django.core.management import call_command
from django.test import TestCase

from supervision.models import Motif, RegleMetier


class SeedBusinessRulesTests(TestCase):
    def test_seed_does_not_create_unvalidated_article_rule(self):
        call_command('seed_bam', verbosity=0)

        self.assertFalse(
            Motif.objects.filter(code_motif='ARTICLE_INVALIDE').exists(),
            'Le motif ARTICLE_INVALIDE ne doit pas être initialisé sans validation métier.',
        )
        self.assertFalse(
            RegleMetier.objects.filter(
                code_regle__in=(
                    'ENVOI_STRUCTURE_ARTICLE',
                    'ENVOI_STRUCTURE_DES_ARTICLE',
                ),
            ).exists(),
            'Aucune règle automatique ARTICLE/DES_ARTICLE ne doit être créée.',
        )

    def test_seed_keeps_confirmed_multicause_fields(self):
        call_command('seed_bam', verbosity=0)

        self.assertTrue(Motif.objects.filter(code_motif='VILLE_MANQUANTE', actif=True).exists())
        self.assertTrue(Motif.objects.filter(code_motif='TELEPHONE_MANQUANT', actif=True).exists())
        self.assertTrue(RegleMetier.objects.filter(code_regle='ENVOI_TELEPHONE_VIDE', actif=True).exists())
