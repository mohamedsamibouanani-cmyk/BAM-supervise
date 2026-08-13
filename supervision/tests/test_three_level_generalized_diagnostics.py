from django.core.management import call_command
from django.test import TestCase

from supervision.models import (
    AttributDefinition, CampagneImport, CampagneSupervision, EnvoiSnapshot,
    FichierImport, ServiceAttributRegle, ServiceSnapshot, Systeme, Superviseur,
    ValeurAttributSnapshot,
)
from supervision.services.comparison import run_campaign
from supervision.services.security import encrypt_sensitive, sensitive_fingerprint
from supervision.services.utils import stable_hash


class ThreeLevelGeneralizedDiagnosisTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(username='three-levels', password='secret12345')
        self.systems = {system.code_systeme: system for system in Systeme.objects.all()}

    def _file(self, campaign_key, code):
        return FichierImport.objects.create(
            systeme=self.systems[code],
            superviseur=self.user,
            nom_fichier=f'{campaign_key}_{code}.xlsx',
            chemin_stockage=f'/tmp/{campaign_key}_{code}.xlsx',
            checksum_sha256=stable_hash(campaign_key, code),
            statut_import='CHARGE',
            nb_lignes_source=1,
            nb_lignes_retenues=1,
        )

    def _campaign(self, key):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {code: self._file(key, code) for code in self.systems}
        for code, file_import in files.items():
            CampagneImport.objects.create(
                campagne=campaign,
                systeme=self.systems[code],
                fichier_import=file_import,
            )
        return campaign, files

    def _shipment(self, file_import, code_envoi, system_code):
        return EnvoiSnapshot.objects.create(
            fichier_import=file_import,
            code_envoi=code_envoi,
            num_commande='CMD-001',
            org_commerciale='2000',
            ligne_premiere=2,
            empreinte_envoi=stable_hash(system_code, code_envoi),
        )

    def _service(self, shipment, system_code, service_code='501'):
        return ServiceSnapshot.objects.create(
            envoi_snapshot=shipment,
            code_service=service_code,
            libelle_service='Service de test',
            ligne_source=2,
            empreinte_service=stable_hash(system_code, shipment.code_envoi, service_code),
        )

    def test_level_1_scans_custom_imported_fields_not_only_seeded_fields(self):
        campaign, files = self._campaign('N1-ALL-FIELDS')
        shipments = {}
        for code in ('SMI', 'SICOM'):
            shipments[code] = self._shipment(files[code], 'E-N1-ALL', code)
            self._service(shipments[code], code)

        custom_attributes = []
        for index in range(1, 5):
            attribute = AttributDefinition.objects.create(
                code_attribut=f'CHAMP_PERSONNALISE_{index}',
                libelle=f'Champ personnalisé {index}',
                portee=AttributDefinition.Portee.ENVOI,
                type_valeur=AttributDefinition.TypeValeur.TEXTE,
            )
            custom_attributes.append(attribute)
            ServiceAttributRegle.objects.create(
                service_ref=None,
                attribut=attribute,
                systeme=self.systems['SIBO'],
                obligatoire=True,
                regle_validation={},
                message_erreur=f'{attribute.code_attribut} obligatoire dans SIBO.',
            )

        for attribute in custom_attributes:
            ValeurAttributSnapshot.objects.create(
                envoi_snapshot=shipments['SMI'],
                attribut=attribute,
                valeur_brute='',
                valeur_normalisee=None,
                est_vide=True,
                format_source_conforme=None,
            )
            ValeurAttributSnapshot.objects.create(
                envoi_snapshot=shipments['SICOM'],
                attribut=attribute,
                valeur_brute='VALEUR OK',
                valeur_normalisee='VALEUR OK',
                est_vide=False,
                format_source_conforme=True,
            )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau='ENVOI', type_ecart='ABSENT', systeme_ecart=self.systems['SIBO']
        )

        fields = {
            prediction.explication.get('attribut_analyse')
            for prediction in anomaly.predictions.all()
        }
        for attribute in custom_attributes:
            self.assertIn(attribute.code_attribut, fields)
        self.assertEqual(anomaly.predictions.count(), 4)
        for prediction in anomaly.predictions.all():
            self.assertEqual(
                prediction.motif.code_motif,
                'CHAMP_OBLIGATOIRE_ENVOI_ABSENT',
            )
            self.assertEqual(prediction.explication['systemes_a_corriger'], ['SMI'])
            self.assertIn('est obligatoire et absent', prediction.explication['message'])

    def test_level_3_missing_amount_can_be_explained_by_target_decimal_format(self):
        campaign, files = self._campaign('N3-DECIMAL')
        shipments = {
            code: self._shipment(files[code], 'E-N3-DECIMAL', code)
            for code in self.systems
        }
        services = {code: self._service(shipments[code], code, '30100') for code in self.systems}
        amount = AttributDefinition.objects.get(code_attribut='MONTANT_CRBT')
        ServiceAttributRegle.objects.create(
            service_ref=None,
            attribut=amount,
            systeme=self.systems['SIBO'],
            obligatoire=True,
            regle_validation={'decimal_separator': '.'},
            message_erreur='SIBO attend le point comme séparateur décimal.',
        )
        ValeurAttributSnapshot.objects.create(
            service_snapshot=services['SMI'], attribut=amount,
            valeur_brute='3,12', valeur_normalisee='3.12',
            est_vide=False, format_source_conforme=True,
        )
        ValeurAttributSnapshot.objects.create(
            service_snapshot=services['SICOM'], attribut=amount,
            valeur_brute='13.12', valeur_normalisee='13.12',
            est_vide=False, format_source_conforme=True,
        )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau='ATTRIBUT', type_ecart='ABSENT', attribut=amount,
            systeme_ecart=self.systems['SIBO'],
        )
        prediction = anomaly.predictions.get(motif__code_motif='FORMAT_MONTANT_INCOMPATIBLE')
        self.assertEqual(prediction.explication['systemes_a_corriger'], ['SMI'])
        self.assertIn('incompatible avec SIBO', prediction.explication['message'])
        self.assertTrue(any(
            'Séparateur décimal attendu : .' in item['constat']
            for item in prediction.explication['indices']
        ))

    def test_level_3_missing_phone_uses_target_specific_regex_without_plaintext_storage(self):
        campaign, files = self._campaign('N3-PHONE')
        shipments = {
            code: self._shipment(files[code], 'E-N3-PHONE', code)
            for code in self.systems
        }
        services = {code: self._service(shipments[code], code, '30801') for code in self.systems}
        phone = AttributDefinition.objects.get(code_attribut='TELEPHONE_NOTIFICATION')
        ServiceAttributRegle.objects.create(
            service_ref=None,
            attribut=phone,
            systeme=self.systems['SIBO'],
            obligatoire=True,
            regle_validation={
                'regex': r'\+212\d{9}',
                'message': 'Format international +212XXXXXXXXX attendu',
            },
            message_erreur='Format téléphone incompatible.',
        )
        raw_phone = '0612345678'
        for code in ('SMI', 'SICOM'):
            ValeurAttributSnapshot.objects.create(
                service_snapshot=services[code],
                attribut=phone,
                valeur_brute=encrypt_sensitive(raw_phone),
                valeur_normalisee=sensitive_fingerprint(raw_phone),
                est_vide=False,
                format_source_conforme=True,
            )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau='ATTRIBUT', type_ecart='ABSENT', attribut=phone,
            systeme_ecart=self.systems['SIBO'],
        )
        prediction = anomaly.predictions.get(motif__code_motif='FORMAT_ATTRIBUT_INCOMPATIBLE')
        self.assertEqual(set(prediction.explication['systemes_a_corriger']), {'SMI', 'SICOM'})
        self.assertIn('incompatible avec SIBO', prediction.explication['message'])
        stored_values = ValeurAttributSnapshot.objects.filter(attribut=phone)
        self.assertTrue(all(value.valeur_brute.startswith('enc:v1:') for value in stored_values))
        self.assertTrue(all(raw_phone not in value.valeur_brute for value in stored_values))

    def test_level_3_does_not_invent_target_format_without_a_configured_requirement(self):
        campaign, files = self._campaign('N3-NO-RULE')
        shipments = {
            code: self._shipment(files[code], 'E-N3-NO-RULE', code)
            for code in self.systems
        }
        services = {code: self._service(shipments[code], code, '30018') for code in self.systems}
        amount = AttributDefinition.objects.get(code_attribut='MONTANT_VALEUR_DECLAREE')
        for code, raw in (('SMI', '3,12'), ('SICOM', '3.12')):
            ValeurAttributSnapshot.objects.create(
                service_snapshot=services[code], attribut=amount,
                valeur_brute=raw, valeur_normalisee='3.12',
                est_vide=False, format_source_conforme=True,
            )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau='ATTRIBUT', type_ecart='ABSENT', attribut=amount,
            systeme_ecart=self.systems['SIBO'],
        )
        self.assertFalse(
            anomaly.predictions.filter(
                motif__code_motif__in=[
                    'FORMAT_MONTANT_INCOMPATIBLE',
                    'FORMAT_ATTRIBUT_INCOMPATIBLE',
                ]
            ).exists()
        )
        self.assertEqual(
            anomaly.predictions.get().motif.code_motif,
            'ATTRIBUT_NON_SYNCHRONISE',
        )
