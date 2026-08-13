from django.core.management import call_command
from django.test import TestCase, override_settings

from supervision.models import (
    Anomalie, AttributDefinition, CampagneImport, CampagneSupervision,
    ContactGroupe, FichierImport, GroupeResponsable, Notification,
    ServiceAttributRegle, ServiceSnapshot, Systeme, Superviseur,
    EnvoiSnapshot, ValidationMotif, ValeurAttributSnapshot,
)
from supervision.services.comparison import run_campaign
from supervision.services.notifications import send_validation_email
from supervision.services.utils import stable_hash


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
    DEFAULT_FROM_EMAIL='notifications@bam-supervise.ma',
)
class StrictEnvoiDiagnosisTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(
            username='strict-envoi', password='secret12345'
        )
        self.systems = {
            system.code_systeme: system
            for system in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])
        }

    def _campaign(self, key):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {}
        for code in self.systems:
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
        return campaign, files

    def _shipment(self, file_import, code_envoi, system_code):
        shipment = EnvoiSnapshot.objects.create(
            fichier_import=file_import,
            code_envoi=code_envoi,
            num_commande='CMD-001',
            org_commerciale='2000',
            ligne_premiere=2,
            empreinte_envoi=stable_hash(code_envoi, system_code),
        )
        service = ServiceSnapshot.objects.create(
            envoi_snapshot=shipment,
            code_service='30100',
            libelle_service='Service test',
            ligne_source=2,
            empreinte_service=stable_hash(code_envoi, system_code, '30100'),
        )
        return shipment, service

    def _contact(self, code):
        group = GroupeResponsable.objects.get(
            systeme=self.systems[code], nom_groupe=f'Groupe {code}'
        )
        ContactGroupe.objects.create(
            groupe=group,
            nom_complet=f'Collaborateur {code}',
            email=f'collaborateur.{code.lower()}@bam-supervise.ma',
        )
        return group

    def test_mandatory_missing_is_the_only_specific_n1_cause(self):
        campaign, files = self._campaign('N1-MANDATORY-FIRST')
        snapshots = {
            code: self._shipment(files[code], 'E-N1-MANDATORY', code)
            for code in ('SMI', 'SICOM')
        }
        city = AttributDefinition.objects.create(
            code_attribut='CHAMP_OBLIGATOIRE_TEST',
            libelle='Champ obligatoire test',
            portee=AttributDefinition.Portee.ENVOI,
            type_valeur=AttributDefinition.TypeValeur.TEXTE,
        )
        price = AttributDefinition.objects.create(
            code_attribut='PRIX_FORMAT_TEST',
            libelle='Prix format test',
            portee=AttributDefinition.Portee.SERVICE,
            type_valeur=AttributDefinition.TypeValeur.NOMBRE,
        )
        ServiceAttributRegle.objects.create(
            attribut=city,
            systeme=self.systems['SIBO'],
            obligatoire=True,
            regle_validation={},
            message_erreur='Champ obligatoire.',
        )
        ServiceAttributRegle.objects.create(
            attribut=price,
            systeme=self.systems['SIBO'],
            obligatoire=False,
            regle_validation={'decimal_separator': '.'},
            message_erreur='Point attendu.',
        )

        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=snapshots['SMI'][0], attribut=city,
            valeur_brute='', valeur_normalisee=None,
            est_vide=True, format_source_conforme=None,
        )
        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=snapshots['SICOM'][0], attribut=city,
            valeur_brute='OK', valeur_normalisee='OK',
            est_vide=False, format_source_conforme=True,
        )
        ValeurAttributSnapshot.objects.create(
            service_snapshot=snapshots['SMI'][1], attribut=price,
            valeur_brute='1.3,12', valeur_normalisee='1.3,12',
            est_vide=False, format_source_conforme=False,
        )
        ValeurAttributSnapshot.objects.create(
            service_snapshot=snapshots['SICOM'][1], attribut=price,
            valeur_brute='13.12', valeur_normalisee='13.12',
            est_vide=False, format_source_conforme=True,
        )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            systeme_ecart=self.systems['SIBO'],
        )
        predictions = list(anomaly.predictions.order_by('rang'))
        self.assertEqual(len(predictions), 1)
        self.assertEqual(
            predictions[0].motif.code_motif,
            'CHAMP_OBLIGATOIRE_ENVOI_ABSENT',
        )
        self.assertEqual(
            predictions[0].explication['message'],
            'Le champ CHAMP_OBLIGATOIRE_TEST est obligatoire et absent',
        )
        self.assertEqual(
            predictions[0].explication['systemes_a_corriger'], ['SMI']
        )
        self.assertFalse(
            anomaly.predictions.filter(
                motif__code_motif='FORMAT_CHAMP_ENVOI_NON_RESPECTE'
            ).exists()
        )

    def test_format_without_missing_mandatory_field_becomes_unknown_at_n1(self):
        campaign, files = self._campaign('N1-NO-FORMAT-MOTIF')
        snapshots = {
            code: self._shipment(files[code], 'E-N1-FORMAT', code)
            for code in ('SMI', 'SICOM')
        }
        price = AttributDefinition.objects.create(
            code_attribut='PRIX_CIBLE_TEST',
            libelle='Prix cible test',
            portee=AttributDefinition.Portee.SERVICE,
            type_valeur=AttributDefinition.TypeValeur.NOMBRE,
        )
        ServiceAttributRegle.objects.create(
            attribut=price,
            systeme=self.systems['SIBO'],
            obligatoire=False,
            regle_validation={'decimal_separator': '.'},
            message_erreur='Le point est attendu.',
        )
        ValeurAttributSnapshot.objects.create(
            service_snapshot=snapshots['SMI'][1], attribut=price,
            valeur_brute='13,12', valeur_normalisee='13.12',
            est_vide=False, format_source_conforme=True,
        )
        ValeurAttributSnapshot.objects.create(
            service_snapshot=snapshots['SICOM'][1], attribut=price,
            valeur_brute='13.12', valeur_normalisee='13.12',
            est_vide=False, format_source_conforme=True,
        )

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ENVOI,
            systeme_ecart=self.systems['SIBO'],
        )
        prediction = anomaly.predictions.get()
        self.assertEqual(prediction.motif.code_motif, 'MOTIF_INCONNU')
        self.assertEqual(prediction.explication['role_diagnostic'], 'MOTIF_NON_IDENTIFIABLE')
        self.assertFalse(
            anomaly.predictions.filter(
                motif__code_motif='FORMAT_CHAMP_ENVOI_NON_RESPECTE'
            ).exists()
        )

    def test_mandatory_missing_email_targets_source_after_validation(self):
        campaign, files = self._campaign('N1-MANDATORY-MAIL')
        snapshots = {
            code: self._shipment(files[code], 'E-N1-MANDATORY-MAIL', code)
            for code in ('SMI', 'SICOM')
        }
        mandatory = AttributDefinition.objects.create(
            code_attribut='CHAMP_OBLIGATOIRE_MAIL',
            libelle='Champ obligatoire mail',
            portee=AttributDefinition.Portee.ENVOI,
            type_valeur=AttributDefinition.TypeValeur.TEXTE,
        )
        ServiceAttributRegle.objects.create(
            attribut=mandatory,
            systeme=self.systems['SIBO'],
            obligatoire=True,
            regle_validation={},
            message_erreur='Champ obligatoire.',
        )
        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=snapshots['SMI'][0], attribut=mandatory,
            valeur_brute='', valeur_normalisee=None,
            est_vide=True, format_source_conforme=None,
        )
        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=snapshots['SICOM'][0], attribut=mandatory,
            valeur_brute='OK', valeur_normalisee='OK',
            est_vide=False, format_source_conforme=True,
        )
        self._contact('SMI')

        run_campaign(campaign)
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ENVOI,
            systeme_ecart=self.systems['SIBO'],
        )
        prediction = anomaly.predictions.get()
        self.assertEqual(prediction.systeme_a_corriger_predit, self.systems['SMI'])
        self.assertEqual(Notification.objects.count(), 0)

        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            prediction_retenue=prediction,
            motif_final=prediction.motif,
            systeme_a_corriger_final=self.systems['SMI'],
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
            commentaire='Renseigner le champ obligatoire.',
            version_validation=1,
            est_finale=True,
        )
        send_validation_email(validation)

        notification = Notification.objects.get()
        self.assertEqual(notification.groupe.systeme, self.systems['SMI'])
        self.assertIn('CHAMP OBLIGATOIRE À RENSEIGNER', notification.objet)
        self.assertIn('Système source à corriger : SMI', notification.message)
        self.assertIn('- CHAMP_OBLIGATOIRE_MAIL', notification.message)
        self.assertIn('renseigner ce ou ces champs dans SMI', notification.message)

    def test_unknown_reason_sends_complete_reentry_data_only_after_validation(self):
        campaign, files = self._campaign('N1-UNKNOWN-VALIDATION')
        shipment, service = self._shipment(files['SMI'], 'E-N1-UNKNOWN', 'SMI')

        envoi_attribute = AttributDefinition.objects.create(
            code_attribut='REFERENCE_CLIENT_TEST',
            libelle='Référence client test',
            portee=AttributDefinition.Portee.ENVOI,
            type_valeur=AttributDefinition.TypeValeur.TEXTE,
        )
        service_attribute = AttributDefinition.objects.create(
            code_attribut='MONTANT_SERVICE_TEST',
            libelle='Montant service test',
            portee=AttributDefinition.Portee.SERVICE,
            type_valeur=AttributDefinition.TypeValeur.NOMBRE,
        )
        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=shipment,
            attribut=envoi_attribute,
            valeur_brute='REF-42',
            valeur_normalisee='REF-42',
            est_vide=False,
            format_source_conforme=True,
        )
        ValeurAttributSnapshot.objects.create(
            service_snapshot=service,
            attribut=service_attribute,
            valeur_brute='750',
            valeur_normalisee='750',
            est_vide=False,
            format_source_conforme=True,
        )
        self._contact('SMI')

        run_campaign(campaign)
        self.assertEqual(Notification.objects.count(), 0)

        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ENVOI,
            systeme_ecart=self.systems['SIBO'],
        )
        prediction = anomaly.predictions.get()
        self.assertEqual(prediction.motif.code_motif, 'MOTIF_INCONNU')
        self.assertEqual(prediction.explication['message'], 'Motif non identifiable')
        self.assertEqual(prediction.systeme_a_corriger_predit, self.systems['SMI'])
        self.assertEqual(Notification.objects.count(), 0)

        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            prediction_retenue=prediction,
            motif_final=prediction.motif,
            systeme_a_corriger_final=self.systems['SMI'],
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
            commentaire='Ressaisir complètement l’envoi.',
            version_validation=1,
            est_finale=True,
        )
        send_validation_email(validation)

        notification = Notification.objects.get()
        self.assertEqual(notification.groupe.systeme, self.systems['SMI'])
        self.assertIn('RESSAISIE ENVOI', notification.objet)
        self.assertIn('ressaisir complètement l’envoi CODE_ENVOI=E-N1-UNKNOWN', notification.message)
        self.assertIn('NUM_COMMANDE = CMD-001', notification.message)
        self.assertIn('CODE_ENVOI = E-N1-UNKNOWN', notification.message)
        self.assertIn('REFERENCE_CLIENT_TEST = REF-42', notification.message)
        self.assertIn('ARTICLE = 30100', notification.message)
        self.assertIn('DES_ARTICLE = Service test', notification.message)
        self.assertIn('MONTANT_SERVICE_TEST = 750', notification.message)
        anomaly.refresh_from_db()
        self.assertEqual(anomaly.statut, Anomalie.Statut.NOTIFIEE)
