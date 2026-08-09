from django.core import mail
from django.test import TestCase, override_settings

from supervision.models import (
    Anomalie, CampagneSupervision, GroupeResponsable, Motif, Notification,
    RegleAffectation, Systeme, Superviseur, ValidationMotif, ContactGroupe,
)
from supervision.services.notifications import RoutingError, send_validation_email


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
)
class EmailWorkflowTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='superviseur', password='secret12345')
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        self.motif = Motif.objects.create(code_motif='VILLE_MANQUANTE', libelle='Ville manquante', niveau_applicable='ENVOI', categorie='DONNEE')
        self.group = GroupeResponsable.objects.create(systeme=self.smi, nom_groupe='Groupe SMI')
        self.contact = ContactGroupe.objects.create(groupe=self.group, nom_complet='Collaborateur 1', email='collab@example.com')
        RegleAffectation.objects.create(systeme_a_corriger=self.smi, motif=self.motif, groupe=self.group)
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(campagne=campaign, niveau='ENVOI', type_ecart='ABSENT', code_envoi='E100', systeme_ecart=self.sibo, empreinte_anomalie='a'*64)
        self.validation = ValidationMotif.objects.create(anomalie=self.anomaly, motif_final=self.motif, systeme_a_corriger_final=self.smi, superviseur=self.user, decision='ACCEPTE')

    def test_email_is_sent_to_group_contact(self):
        notification = send_validation_email(self.validation)
        self.assertEqual(notification.statut, 'ENVOYEE')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('collab@example.com', mail.outbox[0].to)
        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.statut, 'NOTIFIEE')

    def test_legacy_active_flag_does_not_exclude_registered_collaborator(self):
        self.contact.actif = False
        self.contact.save(update_fields=['actif'])

        notification = send_validation_email(self.validation)

        self.assertEqual(notification.statut, Notification.Statut.ENVOYEE)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('collab@example.com', mail.outbox[0].to)
        self.assertTrue(
            notification.destinataires.filter(email_snapshot='collab@example.com').exists()
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend',
        EMAIL_ALLOW_SIMULATED_DELIVERY=False,
    )
    def test_console_backend_cannot_fake_real_delivery(self):
        with self.assertRaises(RoutingError):
            send_validation_email(self.validation)

        notification = Notification.objects.get(validation=self.validation)
        self.assertEqual(notification.statut, Notification.Statut.ECHEC)
        self.assertIn('mode test', notification.erreur)

        self.anomaly.refresh_from_db()
        self.assertNotEqual(self.anomaly.statut, Anomalie.Statut.NOTIFIEE)
