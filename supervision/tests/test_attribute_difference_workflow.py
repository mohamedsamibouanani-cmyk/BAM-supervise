from django.core.management import call_command
from django.test import TestCase, override_settings

from supervision.forms import ValidationMotifForm
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
class AttributeDifferenceWorkflowTests(TestCase):
    def setUp(self):
        call_command('seed_bam', verbosity=0)
        self.user = Superviseur.objects.create_user(
            username='attribute-difference', password='secret12345'
        )
        self.systems = {
            system.code_systeme: system
            for system in Systeme.objects.filter(code_systeme__in=['SMI', 'SICOM', 'SIBO'])
        }
        for code in self.systems:
            group = GroupeResponsable.objects.filter(
                systeme=self.systems[code], actif=True
            ).order_by('id').first()
            ContactGroupe.objects.create(
                groupe=group,
                nom_complet=f'Collaborateur {code}',
                email=f'diff.{code.lower()}@bam-supervise.ma',
            )

    def _campaign(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        attribute = AttributDefinition.objects.get(code_attribut='MONTANT_VALEUR_DECLAREE')
        values = {'SMI': '2000', 'SICOM': '2000', 'SIBO': '1800'}
        for code in ('SMI', 'SICOM', 'SIBO'):
            file_import = FichierImport.objects.create(
                systeme=self.systems[code],
                superviseur=self.user,
                nom_fichier=f'DIFF_{code}.xlsx',
                chemin_stockage=f'/tmp/DIFF_{code}.xlsx',
                checksum_sha256=stable_hash('attribute-difference', code),
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
                code_envoi='LD-DIFF-001',
                num_commande='CMD-DIFF-001',
                org_commerciale='2000',
                ligne_premiere=2,
                empreinte_envoi=stable_hash('LD-DIFF-001', code),
            )
            service = ServiceSnapshot.objects.create(
                envoi_snapshot=shipment,
                code_service='30018',
                libelle_service='Valeur déclarée',
                ligne_source=2,
                empreinte_service=stable_hash('LD-DIFF-001', code, '30018'),
            )
            ValeurAttributSnapshot.objects.create(
                service_snapshot=service,
                attribut=attribute,
                valeur_brute=values[code],
                valeur_normalisee=values[code],
                est_vide=False,
                format_source_conforme=True,
            )
        run_campaign(campaign)
        return campaign

    def test_difference_does_not_choose_target_without_business_rule(self):
        campaign = self._campaign()
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.DIFFERENT,
            attribut__code_attribut='MONTANT_VALEUR_DECLAREE',
        )
        prediction = anomaly.predictions.get()
        self.assertEqual(prediction.motif.code_motif, 'ATTRIBUT_DIFFERENT')
        self.assertIsNone(prediction.systeme_a_corriger_predit)
        self.assertEqual(
            prediction.explication['role_diagnostic'],
            'CONSTAT_ATTRIBUT_DIFFERENT',
        )
        self.assertEqual(prediction.explication['systemes_a_corriger'], [])

        form = ValidationMotifForm(anomaly, data={
            'prediction': prediction.pk,
            'predictions': [prediction.pk],
            'multi_cause_mode': '1',
            'motif_final': prediction.motif_id,
            'nouveau_motif': '',
            'decision': 'MODIFIE',
            'commentaire': '',
            'systeme_a_corriger_final': '',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('Choisissez au moins un système à corriger', str(form.errors))

    def test_supervisor_selected_target_receives_difference_email(self):
        campaign = self._campaign()
        anomaly = campaign.anomalies.get(
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.DIFFERENT,
        )
        prediction = anomaly.predictions.get()
        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            prediction_retenue=prediction,
            motif_final=prediction.motif,
            systeme_a_corriger_final=self.systems['SIBO'],
            superviseur=self.user,
            decision=ValidationMotif.Decision.MODIFIE,
            commentaire='Vérifier la valeur de référence avant correction.',
            version_validation=1,
            est_finale=True,
        )

        self.assertEqual(Notification.objects.count(), 0)
        send_validation_email(validation)
        notification = Notification.objects.get()

        self.assertEqual(notification.groupe.systeme, self.systems['SIBO'])
        self.assertIn('VALEUR ATTRIBUT À VÉRIFIER', notification.objet)
        self.assertIn('- SMI : 2000', notification.message)
        self.assertIn('- SICOM : 2000', notification.message)
        self.assertIn('- SIBO : 1800', notification.message)
        self.assertIn(
            'Aucune règle métier n’est encore configurée pour déterminer automatiquement la valeur de référence',
            notification.message,
        )
