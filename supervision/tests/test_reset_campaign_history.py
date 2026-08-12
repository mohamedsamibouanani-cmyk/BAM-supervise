from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings

from supervision.models import (
    Anomalie,
    AttributDefinition,
    CampagneImport,
    CampagneSupervision,
    ContactGroupe,
    DetailComparaison,
    EnvoiSnapshot,
    ExempleApprentissage,
    FichierImport,
    GroupeResponsable,
    HistoriqueAnomalie,
    JournalAudit,
    ModeleML,
    Motif,
    Notification,
    NotificationDestinataire,
    PredictionMotif,
    ServiceSnapshot,
    Systeme,
    Superviseur,
    ValidationMotif,
    ValeurAttributSnapshot,
    VerificationResolution,
)


class ResetCampaignHistoryTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.tempdir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.supervisor = Superviseur.objects.create_user(
            username='superviseur', password='Strong-Test-Password-2026!'
        )
        self.system = Systeme.objects.create(
            code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1
        )
        self.attribute = AttributDefinition.objects.create(
            code_attribut='VILLE_DESTINATION',
            libelle='Ville destination',
            portee=AttributDefinition.Portee.ENVOI,
            type_valeur=AttributDefinition.TypeValeur.TEXTE,
        )
        self.motif = Motif.objects.create(
            code_motif='VILLE_ABSENTE',
            libelle='Ville absente',
            niveau_applicable=Motif.Niveau.ATTRIBUT,
            categorie='DONNEE',
        )
        self.group = GroupeResponsable.objects.create(
            systeme=self.system, nom_groupe='Équipe SMI'
        )
        self.contact = ContactGroupe.objects.create(
            groupe=self.group, nom_complet='Contact SMI', email='smi@example.com'
        )

        import_path = Path(self.tempdir.name) / 'imports' / 'SMI' / 'source.xlsx.enc'
        import_path.parent.mkdir(parents=True)
        import_path.write_bytes(b'bamfile:v1:test')
        self.import_path = import_path
        imported = FichierImport.objects.create(
            systeme=self.system,
            superviseur=self.supervisor,
            nom_fichier='source.xlsx',
            chemin_stockage=str(import_path),
            checksum_sha256='a' * 64,
            statut_import=FichierImport.Statut.CHARGE,
        )
        shipment = EnvoiSnapshot.objects.create(
            fichier_import=imported,
            code_envoi='EE0001',
            ligne_premiere=2,
            empreinte_envoi='b' * 64,
        )
        service = ServiceSnapshot.objects.create(
            envoi_snapshot=shipment,
            code_service='CRBT',
            numero_occurrence=1,
            ligne_source=2,
            empreinte_service='c' * 64,
        )
        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=shipment,
            attribut=self.attribute,
            valeur_brute='Rabat',
            valeur_normalisee='RABAT',
        )
        self.assertTrue(service.pk)

        campaign = CampagneSupervision.objects.create(superviseur=self.supervisor)
        CampagneImport.objects.create(
            campagne=campaign, systeme=self.system, fichier_import=imported
        )
        anomaly = Anomalie.objects.create(
            campagne=campaign,
            niveau=Anomalie.Niveau.ATTRIBUT,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='EE0001',
            attribut=self.attribute,
            systeme_ecart=self.system,
            empreinte_anomalie='d' * 64,
        )
        DetailComparaison.objects.create(
            anomalie=anomaly,
            systeme=self.system,
            objet_present=False,
            est_ecart=True,
        )
        prediction = PredictionMotif.objects.create(
            anomalie=anomaly,
            source_prediction=PredictionMotif.Source.APPRENTISSAGE,
            motif=self.motif,
            systeme_a_corriger_predit=self.system,
            rang=1,
            score_confiance='0.7500',
        )
        validation = ValidationMotif.objects.create(
            anomalie=anomaly,
            prediction_retenue=prediction,
            motif_final=self.motif,
            systeme_a_corriger_final=self.system,
            superviseur=self.supervisor,
            decision=ValidationMotif.Decision.ACCEPTE,
        )
        ExempleApprentissage.objects.create(
            validation=validation,
            motif_label=self.motif,
            systeme_a_corriger_label=self.system,
            caracteristiques={},
        )
        notification = Notification.objects.create(
            validation=validation,
            groupe=self.group,
            objet='Correction',
            message='À corriger',
        )
        NotificationDestinataire.objects.create(
            notification=notification,
            contact=self.contact,
            nom_snapshot=self.contact.nom_complet,
            email_snapshot=self.contact.email,
        )
        HistoriqueAnomalie.objects.create(
            anomalie=anomaly,
            nouveau_statut=Anomalie.Statut.VALIDEE,
            source_evenement='SUPERVISEUR',
            superviseur=self.supervisor,
        )
        VerificationResolution.objects.create(
            anomalie=anomaly,
            campagne_controle=campaign,
            resultat=VerificationResolution.Resultat.PERSISTANTE,
        )

        model_path = Path(self.tempdir.name) / 'models' / 'motifs.joblib'
        model_path.parent.mkdir(parents=True)
        model_path.write_bytes(b'model')
        self.model_path = model_path
        ModeleML.objects.create(
            nom_modele='Motifs',
            algorithme='Test',
            version_modele='v-test',
            entraine_le=campaign.demarree_le,
            nb_exemples=1,
            metriques={},
            chemin_fichier=str(model_path),
            checksum_sha256='e' * 64,
            actif=True,
        )
        JournalAudit.objects.create(
            superviseur=self.supervisor,
            action='CREATE',
            entite='campagne_supervision',
            id_entite=campaign.pk,
        )
        JournalAudit.objects.create(
            superviseur=self.supervisor,
            action='CREATE',
            entite='contact_groupe',
            id_entite=self.contact.pk,
        )

    def test_confirmation_is_mandatory(self):
        with self.assertRaises(CommandError):
            call_command('reset_campaign_history', confirm='NON')
        self.assertEqual(CampagneSupervision.objects.count(), 1)
        self.assertTrue(self.import_path.exists())

    def test_reset_removes_execution_history_and_preserves_configuration(self):
        stdout = StringIO()
        call_command('reset_campaign_history', confirm='SUPPRIMER', stdout=stdout)

        for model in (
            CampagneSupervision,
            CampagneImport,
            FichierImport,
            EnvoiSnapshot,
            ServiceSnapshot,
            ValeurAttributSnapshot,
            Anomalie,
            DetailComparaison,
            PredictionMotif,
            ValidationMotif,
            ExempleApprentissage,
            Notification,
            NotificationDestinataire,
            VerificationResolution,
            HistoriqueAnomalie,
            ModeleML,
        ):
            self.assertEqual(model.objects.count(), 0, model.__name__)

        self.assertFalse(self.import_path.exists())
        self.assertFalse(self.model_path.exists())
        self.assertTrue(Superviseur.objects.filter(pk=self.supervisor.pk).exists())
        self.assertTrue(Systeme.objects.filter(pk=self.system.pk).exists())
        self.assertTrue(AttributDefinition.objects.filter(pk=self.attribute.pk).exists())
        self.assertTrue(Motif.objects.filter(pk=self.motif.pk).exists())
        self.assertTrue(GroupeResponsable.objects.filter(pk=self.group.pk).exists())
        self.assertTrue(ContactGroupe.objects.filter(pk=self.contact.pk).exists())
        self.assertFalse(JournalAudit.objects.filter(entite='campagne_supervision').exists())
        self.assertTrue(JournalAudit.objects.filter(entite='contact_groupe').exists())

        first_campaign = CampagneSupervision.objects.create(superviseur=self.supervisor)
        self.assertEqual(first_campaign.pk, 1)
        self.assertIn('Historique des campagnes supprimé', stdout.getvalue())
