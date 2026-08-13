from django.core.management import call_command
from django.test import TestCase, override_settings

from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    ContactGroupe, EnvoiSnapshot, FichierImport, GroupeResponsable,
    Notification, ServiceSnapshot, Systeme, Superviseur, ValidationMotif,
    ValeurAttributSnapshot,
)
from supervision.services.comparison import run_campaign
from supervision.services.notifications import send_validation_email
from supervision.services.utils import stable_hash


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
    DEFAULT_FROM_EMAIL='notifications@bam-supervise.ma',
)
class AttributeMissingFillNotificationTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(
            username='attribute-fill', password='secret12345'
        )
        self.systems = {
            system.code_systeme: system
            for system in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])
        }
        for code in ('SMI', 'SIBO'):
            group = GroupeResponsable.objects.filter(
                systeme=self.systems[code], actif=True
            ).order_by('id').first()
            self.assertIsNotNone(group)
            ContactGroupe.objects.create(
                groupe=group,
                nom_complet=f'Collaborateur {code}',
                email=f'collaborateur.{code.lower()}@bam-supervise.ma',
            )

    def _campaign_with_missing_attribute(self, key, source_values, invalid_systems=None):
        invalid_systems = set(invalid_systems or [])
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        services = {}
        for code in ('SMI', 'SICOM', 'SIBO'):
            file_import = FichierImport.objects.create(
                systeme=self.systems[code],
                superviseur=self.user,
                nom_fichier=f'{key}_{code}.xlsx',
                chemin_stockage=f'/tmp/{key}_{code}.xlsx',
                checksum_sha256=stable_hash(key, code),
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
                code_envoi=f'E-{key}',
                num_commande=f'CMD-{key}',
                org_commerciale='2000',
                ligne_premiere=2,
                empreinte_envoi=stable_hash(key, code, 'shipment'),
            )
            services[code] = ServiceSnapshot.objects.create(
                envoi_snapshot=shipment,
                code_service='30100',
                libelle_service='Service CRBT',
                ligne_source=2,
                empreinte_service=stable_hash(key, code, '30100'),
            )

        attribute = AttributDefinition.objects.get(code_attribut='MONTANT_CRBT')
        for code, raw in source_values.items():
            ValeurAttributSnapshot.objects.create(
                service_snapshot=services[code],
                attribut=attribute,
                valeur_brute=str(raw),
                valeur_normalisee=str(raw),
                est_vide=False,
                format_source_conforme=(code not in invalid_systems),
            )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            attribut=attribute,
            systeme_ecart=self.systems['SIBO'],
        )
        prediction = anomaly.predictions.get()
        return anomaly, prediction

    def _validate_and_send(self, anomaly, prediction, target_code):
        self.assertEqual(Notification.objects.count(), 0)
        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            prediction_retenue=prediction,
            motif_final=prediction.motif,
            systeme_a_corriger_final=self.systems[target_code],
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
            commentaire='Traiter l’attribut non synchronisé.',
            version_validation=1,
            est_finale=True,
        )
        self.assertEqual(Notification.objects.count(), 0)
        send_validation_email(validation)
        return Notification.objects.get()

    def test_same_source_value_is_sent_to_missing_system_after_validation(self):
        anomaly, prediction = self._campaign_with_missing_attribute(
            'SAME', {'SMI': '800', 'SICOM': '800'}
        )
        self.assertEqual(prediction.motif.code_motif, 'ATTRIBUT_NON_SYNCHRONISE')
        self.assertEqual(prediction.systeme_a_corriger_predit, self.systems['SIBO'])
        notification = self._validate_and_send(anomaly, prediction, 'SIBO')

        self.assertEqual(notification.groupe.systeme, self.systems['SIBO'])
        self.assertIn('ATTRIBUT À RENSEIGNER', notification.objet)
        self.assertIn('Aucun motif de format n’a été identifié.', notification.message)
        self.assertIn('- SMI : 800', notification.message)
        self.assertIn('- SICOM : 800', notification.message)
        self.assertIn('avec la valeur : 800.', notification.message)

    def test_different_source_values_are_listed_without_automatic_choice(self):
        anomaly, prediction = self._campaign_with_missing_attribute(
            'DIFF', {'SMI': '800', 'SICOM': '900'}
        )
        self.assertEqual(prediction.motif.code_motif, 'ATTRIBUT_NON_SYNCHRONISE')
        notification = self._validate_and_send(anomaly, prediction, 'SIBO')

        self.assertEqual(notification.groupe.systeme, self.systems['SIBO'])
        self.assertIn('- SMI : 800', notification.message)
        self.assertIn('- SICOM : 900', notification.message)
        self.assertIn(
            'BAM Supervise ne choisit pas automatiquement une valeur de référence',
            notification.message,
        )

    def test_invalid_source_format_is_routed_to_source_after_validation(self):
        anomaly, prediction = self._campaign_with_missing_attribute(
            'FORMAT',
            {'SMI': '1.3,12', 'SICOM': '13.12'},
            invalid_systems={'SMI'},
        )
        self.assertEqual(prediction.motif.code_motif, 'FORMAT_MONTANT_INCOMPATIBLE')
        self.assertEqual(prediction.systeme_a_corriger_predit, self.systems['SMI'])
        self.assertEqual(Notification.objects.count(), 0)

        notification = self._validate_and_send(anomaly, prediction, 'SMI')

        self.assertEqual(notification.groupe.systeme, self.systems['SMI'])
        self.assertIn('Format source non conforme', notification.message)
        self.assertIn('Système à corriger : SMI', notification.message)
