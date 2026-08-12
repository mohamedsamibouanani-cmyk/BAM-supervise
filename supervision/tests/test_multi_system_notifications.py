from django.core import mail
from django.test import TestCase, override_settings

from supervision.models import (
    Anomalie, CampagneSupervision, ContactGroupe, GroupeResponsable, Motif,
    Notification, PredictionMotif, Systeme, Superviseur, ValidationMotif,
)
from supervision.services.notifications import send_validation_email


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
)
class MultiSystemNotificationTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='multi', password='secret12345')
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)

        self.group_smi = GroupeResponsable.objects.create(systeme=self.smi, nom_groupe='Groupe SMI')
        self.group_sicom = GroupeResponsable.objects.create(systeme=self.sicom, nom_groupe='Groupe SICOM')
        self.group_sibo = GroupeResponsable.objects.create(systeme=self.sibo, nom_groupe='Groupe SIBO')
        ContactGroupe.objects.create(
            groupe=self.group_smi, nom_complet='Collaborateur SMI', email='smi@example.com'
        )
        ContactGroupe.objects.create(
            groupe=self.group_sicom, nom_complet='Collaborateur SICOM', email='sicom@example.com'
        )
        ContactGroupe.objects.create(
            groupe=self.group_sibo, nom_complet='Collaborateur SIBO', email='sibo@example.com'
        )

        self.motif = Motif.objects.create(
            code_motif='VILLE_MANQUANTE',
            libelle='Ville obligatoire manquante',
            niveau_applicable='ENVOI',
            categorie='DONNEE',
            champ_typique='VILLE',
        )
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=campaign,
            niveau='ENVOI',
            type_ecart='ABSENT',
            code_envoi='E-MULTI',
            systeme_ecart=self.sibo,
            empreinte_anomalie='m' * 64,
        )
        # Simule un ancien dossier dont la cible persistée était incorrectement
        # SIBO, alors que les preuves identifient bien SMI et SICOM comme sources
        # contenant la cause. Le routage doit suivre les preuves, jamais la cible stale.
        self.prediction = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.APPRENTISSAGE,
            motif=self.motif,
            systeme_a_corriger_predit=self.sibo,
            rang=1,
            score_confiance='0.9500',
            explication={
                'systemes_a_corriger': ['SIBO'],
                'indices': [
                    {'systeme': 'SMI', 'champ': 'VILLE', 'constat': 'Champ vide ou absent dans la source'},
                    {'systeme': 'SICOM', 'champ': 'VILLE', 'constat': 'Champ vide ou absent dans la source'},
                ],
            },
        )

    def test_accepted_prediction_notifies_evidence_systems_and_never_observed_sibo(self):
        validation = ValidationMotif.objects.create(
            anomalie=self.anomaly,
            prediction_retenue=self.prediction,
            motif_final=self.motif,
            # Même le champ historique est volontairement faux pour vérifier que
            # le service d'envoi ne lui fait pas confiance pour une acceptation.
            systeme_a_corriger_final=self.sibo,
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
        )

        send_validation_email(validation)

        notifications = Notification.objects.filter(validation=validation).select_related('groupe__systeme')
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(
            set(notifications.values_list('groupe__systeme__code_systeme', flat=True)),
            {'SMI', 'SICOM'},
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual({message.to[0] for message in mail.outbox}, {'smi@example.com', 'sicom@example.com'})
        self.assertFalse(Notification.objects.filter(validation=validation, groupe__systeme=self.sibo).exists())
        self.assertFalse(any('sibo@example.com' in message.to for message in mail.outbox))
        self.assertTrue(any('Système responsable de la correction : SMI' in message.body for message in mail.outbox))
        self.assertTrue(any('Système responsable de la correction : SICOM' in message.body for message in mail.outbox))
        self.assertFalse(any('Système responsable de la correction : SIBO' in message.body for message in mail.outbox))

        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.statut, Anomalie.Statut.NOTIFIEE)

    def test_modified_decision_uses_supervisor_explicit_target_only(self):
        validation = ValidationMotif.objects.create(
            anomalie=self.anomaly,
            prediction_retenue=self.prediction,
            motif_final=self.motif,
            systeme_a_corriger_final=self.sicom,
            superviseur=self.user,
            decision=ValidationMotif.Decision.MODIFIE,
        )

        send_validation_email(validation)

        notifications = Notification.objects.filter(validation=validation).select_related('groupe__systeme')
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(notifications.get().groupe.systeme.code_systeme, 'SICOM')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sicom@example.com'])
