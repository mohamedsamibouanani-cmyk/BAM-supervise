from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PreserveNotifiedDifferenceAnomaliesMigrationTests(TransactionTestCase):
    migrate_from = [('supervision', '0002_repair_legacy_prepared_campaigns')]
    migrate_to = [('supervision', '0004_restore_attribute_value_differences')]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        Superviseur = old_apps.get_model('supervision', 'Superviseur')
        Systeme = old_apps.get_model('supervision', 'Systeme')
        GroupeResponsable = old_apps.get_model('supervision', 'GroupeResponsable')
        AttributDefinition = old_apps.get_model('supervision', 'AttributDefinition')
        Motif = old_apps.get_model('supervision', 'Motif')
        CampagneSupervision = old_apps.get_model('supervision', 'CampagneSupervision')
        Anomalie = old_apps.get_model('supervision', 'Anomalie')
        ValidationMotif = old_apps.get_model('supervision', 'ValidationMotif')
        Notification = old_apps.get_model('supervision', 'Notification')

        supervisor = Superviseur.objects.create(
            username='migration-superviseur',
            password='!',
        )
        system = Systeme.objects.create(
            code_systeme='SIBO',
            nom_systeme='SIBO',
            ordre_comparaison=1,
        )
        group = GroupeResponsable.objects.create(
            nom_groupe='Equipe SIBO',
            systeme_id=system.pk,
        )
        attribute = AttributDefinition.objects.create(
            code_attribut='CRBT',
            libelle='Contre remboursement',
            portee='SERVICE',
            type_valeur='NOMBRE',
        )
        motif = Motif.objects.create(
            code_motif='VALEUR_DIFFERENTE',
            libelle='Valeur différente',
            niveau_applicable='ATTRIBUT',
            categorie='SYNCHRONISATION',
        )
        campaign = CampagneSupervision.objects.create(superviseur_id=supervisor.pk)
        anomaly = Anomalie.objects.create(
            campagne_id=campaign.pk,
            niveau='ATTRIBUT',
            type_ecart='DIFFERENT',
            code_envoi='DEMO-001',
            code_service='CRBT',
            attribut_id=attribute.pk,
            systeme_ecart_id=system.pk,
            empreinte_anomalie='a' * 64,
            statut='NOTIFIEE',
        )
        validation = ValidationMotif.objects.create(
            anomalie_id=anomaly.pk,
            motif_final_id=motif.pk,
            systeme_a_corriger_final_id=system.pk,
            superviseur_id=supervisor.pk,
            decision='ACCEPTE',
        )
        notification = Notification.objects.create(
            validation_id=validation.pk,
            groupe_id=group.pk,
            objet='Correction requise',
            message='Corriger la valeur CRBT dans SIBO.',
            statut='ENVOYEE',
        )
        self.anomaly_pk = anomaly.pk
        self.notification_pk = notification.pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_notified_difference_anomaly_is_preserved(self):
        Anomalie = self.apps.get_model('supervision', 'Anomalie')
        Notification = self.apps.get_model('supervision', 'Notification')

        anomaly = Anomalie.objects.get(pk=self.anomaly_pk)
        self.assertEqual(anomaly.type_ecart, 'DIFFERENT')
        self.assertTrue(Notification.objects.filter(pk=self.notification_pk).exists())
