from django.core.management import call_command
from django.test import TestCase

from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    EnvoiSnapshot, FichierImport, Motif, RegleMetier, ServiceSnapshot,
    Systeme, Superviseur, ValeurAttributSnapshot,
)
from supervision.services.comparison import run_campaign
from supervision.services.utils import stable_hash


class AttributeSyncIsolationTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(
            username='attribute-isolation', password='secret12345'
        )
        self.systems = {
            system.code_systeme: system
            for system in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])
        }

    def _file(self, campaign, code):
        file_import = FichierImport.objects.create(
            systeme=self.systems[code],
            superviseur=self.user,
            nom_fichier=f'attribute_sync_{code}.xlsx',
            chemin_stockage=f'/tmp/attribute_sync_{code}.xlsx',
            checksum_sha256=stable_hash('attribute-sync', code),
            statut_import='CHARGE',
            nb_lignes_source=1,
            nb_lignes_retenues=1,
        )
        CampagneImport.objects.create(
            campagne=campaign,
            systeme=self.systems[code],
            fichier_import=file_import,
        )
        return file_import

    def _shipment_with_service(self, file_import, code):
        shipment = EnvoiSnapshot.objects.create(
            fichier_import=file_import,
            code_envoi='LD860711821MA',
            num_commande='CMD-ATTR-001',
            org_commerciale='2000',
            ligne_premiere=2,
            empreinte_envoi=stable_hash('LD860711821MA', code),
        )
        service = ServiceSnapshot.objects.create(
            envoi_snapshot=shipment,
            code_service='30100',
            libelle_service='Service test',
            ligne_source=2,
            empreinte_service=stable_hash('LD860711821MA', code, '30100'),
        )
        return shipment, service

    def test_missing_crbt_never_receives_an_unrelated_city_motif(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {code: self._file(campaign, code) for code in self.systems}
        snapshots = {
            code: self._shipment_with_service(files[code], code)
            for code in self.systems
        }

        crbt = AttributDefinition.objects.get(code_attribut='MONTANT_CRBT')
        ValeurAttributSnapshot.objects.create(
            service_snapshot=snapshots['SMI'][1],
            attribut=crbt,
            valeur_brute='800',
            valeur_normalisee='800',
            est_vide=False,
            format_source_conforme=True,
        )

        # Reproduit une ancienne règle erronée encore présente dans une base locale.
        city = AttributDefinition.objects.get(code_attribut='VILLE')
        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=snapshots['SMI'][0],
            attribut=city,
            valeur_brute='',
            valeur_normalisee=None,
            est_vide=True,
            format_source_conforme=None,
        )
        city_motif = Motif.objects.get(code_motif='VILLE_MANQUANTE')
        RegleMetier.objects.create(
            code_regle='LEGACY_ATTRIBUT_VILLE_VIDE_REPRO',
            attribut=city,
            motif_suggere=city_motif,
            niveau_anomalie='ATTRIBUT',
            type_controle='VIDE',
            expression_regle={},
            seuil_confiance='0.9500',
            priorite=1,
            actif=True,
        )

        run_campaign(campaign)

        anomalies = campaign.anomalies.filter(
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            attribut=crbt,
        ).select_related('systeme_ecart')
        self.assertEqual(anomalies.count(), 2)

        for anomaly in anomalies:
            codes = list(
                anomaly.predictions.order_by('rang')
                .values_list('motif__code_motif', flat=True)
            )
            self.assertEqual(codes, ['ATTRIBUT_NON_SYNCHRONISE'])
            prediction = anomaly.predictions.get()
            self.assertEqual(
                prediction.explication.get('attribut_analyse'),
                'MONTANT_CRBT',
            )
            self.assertEqual(
                prediction.explication.get('systemes_a_corriger'),
                [anomaly.systeme_ecart.code_systeme],
            )
            self.assertNotIn('VILLE_MANQUANTE', codes)
