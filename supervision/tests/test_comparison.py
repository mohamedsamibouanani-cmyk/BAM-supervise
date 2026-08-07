from django.test import TestCase

from supervision.models import (
    CampagneImport, CampagneSupervision, EnvoiSnapshot, FichierImport,
    Systeme, Superviseur,
)
from supervision.services.comparison import run_campaign
from supervision.services.utils import stable_hash


class ComparisonTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='superviseur', password='secret12345')
        self.systems = {}
        for i, code in enumerate(['SMI', 'SICOM', 'SIBO'], 1):
            self.systems[code] = Systeme.objects.create(code_systeme=code, nom_systeme=code, ordre_comparaison=i)

    def _file(self, code):
        return FichierImport.objects.create(
            systeme=self.systems[code], superviseur=self.user, nom_fichier=f'{code}.xlsx',
            chemin_stockage=f'/tmp/{code}.xlsx', checksum_sha256=(code.lower()*64)[:64],
            statut_import='CHARGE', nb_lignes_source=1, nb_lignes_retenues=1,
        )

    def test_missing_envoi_is_detected(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {code: self._file(code) for code in self.systems}
        for code, f in files.items():
            CampagneImport.objects.create(campagne=campaign, systeme=self.systems[code], fichier_import=f)
        for code in ('SMI', 'SIBO'):
            EnvoiSnapshot.objects.create(
                fichier_import=files[code], code_envoi='E001', org_commerciale='2000',
                ligne_premiere=2, empreinte_envoi=stable_hash(code, 'E001')
            )
        run_campaign(campaign)
        anomaly = campaign.anomalies.get(niveau='ENVOI')
        self.assertEqual(anomaly.systeme_ecart.code_systeme, 'SICOM')
        self.assertEqual(anomaly.type_ecart, 'ABSENT')
