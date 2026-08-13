from django.core.management import call_command
from django.test import TestCase, override_settings

from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    ContactGroupe, EnvoiSnapshot, FichierImport, GroupeResponsable,
    Notification, Systeme, Superviseur, ValidationMotif,
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
        group = GroupeResponsable.objects.filter(
            systeme=self.systems['SIBO'], actif=True
        ).order_by('id').first()
        self.assertIsNotNone(group)
        ContactGroupe.objects.create(
            groupe=group,
            nom_complet='Collaborateur SIBO',
            email='collaborateur.sibo@bam-supervise.ma',
        )

    def _campaign_with_missing_attribute(self, key, source_values):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {}
        shipments = {}
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
            files[code] = file_import
            shipments[code] = EnvoiSnapshot.objects.create(
                fichier_import=file_import,
                code_envoi=f'E-{key}',
                num_commande=f'CMD-{key}',
                org_commerciale='2000',
                ligne_premiere=2,
                empreinte_envoi=stable_hash(key, code, 'shipment'),
            )

        attribute = AttributDefinition.objects.create(
            code_attribut=f'ATTR_{key}',
            libelle=f'Attribut {key}',
            portee=AttributDefinition.Portee.ENVOI,
            type_valeur=AttributDefinition.TypeValeur.NOMBRE,
        )
        for code, raw in source_values.items():
            ValeurAttributSnapshot.objects.create(
                envoi_snapshot=shipments[code],
                attribut=attribute,
                valeur_brute=str(raw),
                valeur_normalisee=str(raw),
                est_vide=False,
                format_source_conforme=True,
            )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            attribut=attribute,
            systeme_ecart=self.systems['SIBO'],
        )
        prediction = anomaly.predictions.get()
        self.assertEqual(prediction.motif.code_motif, 'ATTRIBUT_NON_SYNCHRONISE')
        self.assertEqual(prediction.systeme_a_corriger_predit, self.systems['SIBO'])
        return anomaly, prediction

    def _validate_and_send(self, anomaly, prediction):
        self.assertEqual(Notification.objects.count(), 0)
        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            prediction_retenue=prediction,
            motif_final=prediction.motif,
            systeme_a_corriger_final=self.systems['SIBO'],
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
            commentaire='Compléter l’attribut manquant.',
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
        notification = self._validate_and_send(anomaly, prediction)

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
        notification = self._validate_and_send(anomaly, prediction)

        self.assertEqual(notification.groupe.systeme, self.systems['SIBO'])
        self.assertIn('- SMI : 800', notification.message)
        self.assertIn('- SICOM : 900', notification.message)
        self.assertIn(
            'BAM Supervise ne choisit pas automatiquement une valeur de référence',
            notification.message,
        )
