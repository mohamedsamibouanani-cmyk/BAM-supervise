from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    EnvoiSnapshot, FichierImport, ServiceSnapshot, Systeme, Superviseur,
    ValeurAttributSnapshot,
)
from supervision.services.comparison import run_campaign
from supervision.services.security import encrypt_sensitive, sensitive_fingerprint
from supervision.services.utils import stable_hash


class SensitiveAttributeDisplayTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(
            username='sensitive-display', password='secret12345'
        )
        self.systems = {
            system.code_systeme: system
            for system in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])
        }

    def test_supervisor_sees_real_phone_values_while_comparison_details_stay_masked(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        attribute = AttributDefinition.objects.get(code_attribut='TELEPHONE_NOTIFICATION')
        phones = {
            'SMI': '0612345678',
            'SICOM': '0612345678',
            'SIBO': '0699999999',
        }

        for code, phone in phones.items():
            file_import = FichierImport.objects.create(
                systeme=self.systems[code],
                superviseur=self.user,
                nom_fichier=f'PHONE_{code}.xlsx',
                chemin_stockage=f'/tmp/PHONE_{code}.xlsx',
                checksum_sha256=stable_hash('phone-display', code),
                statut_import='CHARGE',
                nb_lignes_source=1,
                nb_lignes_retenues=1,
            )
            CampagneImport.objects.create(
                campagne=campaign,
                systeme=self.systems[code],
                fichier_import=file_import,
            )
            shipment = EnvoiSnapshot.objects.create(
                fichier_import=file_import,
                code_envoi='SIM-PHONE-DIFF',
                num_commande='CMD-PHONE-DIFF',
                org_commerciale='2000',
                ligne_premiere=2,
                empreinte_envoi=stable_hash('SIM-PHONE-DIFF', code),
            )
            service = ServiceSnapshot.objects.create(
                envoi_snapshot=shipment,
                code_service='30100',
                libelle_service='Notification téléphone',
                ligne_source=2,
                empreinte_service=stable_hash('SIM-PHONE-DIFF', code, '30100'),
            )
            ValeurAttributSnapshot.objects.create(
                service_snapshot=service,
                attribut=attribute,
                valeur_brute=encrypt_sensitive(phone),
                valeur_normalisee=sensitive_fingerprint(phone),
                est_vide=False,
                format_source_conforme=True,
            )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.DIFFERENT,
            attribut=attribute,
        )

        self.assertTrue(
            all(value == '••••••••' for value in anomaly.details.values_list('valeur_brute', flat=True))
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('anomaly_detail', args=[anomaly.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '0612345678')
        self.assertContains(response, '0699999999')
