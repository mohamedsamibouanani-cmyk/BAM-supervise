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

    def test_mandatory_missing_has_priority_over_format(self):
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

    def test_format_is_checked_only_when_mandatory_fields_are_complete(self):
        campaign, files = self._campaign('N1-FORMAT-SECOND')
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
        self.assertEqual(
            prediction.motif.code_motif,
            'FORMAT_CHAMP_ENVOI_NON_RESPECTE',
        )
        self.assertEqual(
            prediction.explication['message'],
            'Le format du champ PRIX_CIBLE_TEST n’est pas respecté',
        )
        self.assertEqual(prediction.explication['systemes_a_corriger'], ['SMI'])

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        EMAIL_ALLOW_SIMULATED_DELIVERY=True,
        DEFAULT_FROM_EMAIL='notifications@bam-supervise.ma',
    )
    def test_unknown_reason_never_sends_before_supervisor_validation(self):
        campaign, files = self._campaign('N1-UNKNOWN-VALIDATION')
        self._shipment(files['SMI'], 'E-N1-UNKNOWN', 'SMI')

        group = GroupeResponsable.objects.get(
            systeme=self.systems['SMI'], nom_groupe='Groupe SMI'
        )
        ContactGroupe.objects.create(
            groupe=group,
            nom_complet='Collaborateur SMI',
            email='collaborateur.smi@bam-supervise.ma',
        )

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
            commentaire='Relancer la synchronisation.',
            version_validation=1,
            est_finale=True,
        )
        send_validation_email(validation)

        notification = Notification.objects.get()
        self.assertEqual(notification.groupe.systeme, self.systems['SMI'])
        self.assertIn('RELANCE ENVOI', notification.objet)
        self.assertIn('Merci de relancer l’envoi / la synchronisation', notification.message)
        anomaly.refresh_from_db()
        self.assertEqual(anomaly.statut, Anomalie.Statut.NOTIFIEE)
