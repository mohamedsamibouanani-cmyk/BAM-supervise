from django.test import TestCase

from supervision.models import (
    AttributDefinition, CampagneImport, CampagneSupervision, EnvoiSnapshot,
    FichierImport, Systeme, Superviseur, ValeurAttributSnapshot,
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

    def test_different_attribute_values_create_synchronization_anomaly(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {code: self._file(code) for code in self.systems}
        city = AttributDefinition.objects.create(
            code_attribut='VILLE', libelle='Ville', portee='ENVOI', type_valeur='TEXTE'
        )
        for code, value in zip(self.systems, ('RABAT', 'RABAT', 'CASABLANCA')):
            CampagneImport.objects.create(
                campagne=campaign, systeme=self.systems[code], fichier_import=files[code]
            )
            shipment = EnvoiSnapshot.objects.create(
                fichier_import=files[code], code_envoi='E-DIFF', org_commerciale='2000',
                ligne_premiere=2, empreinte_envoi=stable_hash(code, 'E-DIFF'),
            )
            ValeurAttributSnapshot.objects.create(
                envoi_snapshot=shipment, attribut=city, valeur_brute=value,
                valeur_normalisee=value, est_vide=False, format_source_conforme=True,
            )

        run_campaign(campaign)

        anomaly = campaign.anomalies.get(niveau='ATTRIBUT', type_ecart='DIFFERENT')
        self.assertEqual(anomaly.attribut, city)
        self.assertEqual(anomaly.systeme_ecart.code_systeme, 'SIBO')
        values = {detail.systeme.code_systeme: detail.valeur_brute for detail in anomaly.details.all()}
        self.assertEqual(values, {'SMI': 'RABAT', 'SICOM': 'RABAT', 'SIBO': 'CASABLANCA'})

    def test_equivalent_normalized_attribute_values_do_not_create_anomaly(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {code: self._file(code) for code in self.systems}
        amount = AttributDefinition.objects.create(
            code_attribut='MNT_TTC', libelle='Montant TTC', portee='ENVOI', type_valeur='NOMBRE'
        )
        for code, raw in zip(self.systems, ('1000', '1 000', '1000,00')):
            CampagneImport.objects.create(campagne=campaign, systeme=self.systems[code], fichier_import=files[code])
            shipment = EnvoiSnapshot.objects.create(
                fichier_import=files[code], code_envoi='E-EQUAL', org_commerciale='2000',
                ligne_premiere=2, empreinte_envoi=stable_hash(code, 'E-EQUAL'),
            )
            ValeurAttributSnapshot.objects.create(
                envoi_snapshot=shipment, attribut=amount, valeur_brute=raw,
                valeur_normalisee='1000', est_vide=False, format_source_conforme=True,
            )

        run_campaign(campaign)

        self.assertFalse(campaign.anomalies.exists())
