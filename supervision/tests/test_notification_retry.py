from django.core import mail
from django.test import TestCase, override_settings

from supervision.models import (
    Anomalie, CampagneSupervision, ContactGroupe, GroupeResponsable, Motif,
    Notification, PredictionMotif, Systeme, Superviseur, ValidationMotif,
)
from supervision.services.notification_retry import retry_failed_notification
from supervision.services.notifications import RoutingError


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
)
class NotificationRetryTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='retry-supervisor', password='Strong-Test-Password-2026!'
        )
        self.systems = {}
        self.groups = {}
        for order, code in enumerate(('SMI', 'SICOM', 'SIBO'), start=1):
            system = Systeme.objects.create(
                code_systeme=code, nom_systeme=code, ordre_comparaison=order
            )
            group = GroupeResponsable.objects.create(
                systeme=system, nom_groupe=f'Groupe {code}'
            )
            ContactGroupe.objects.create(
                groupe=group,
                nom_complet=f'Contact {code}',
                email=f'{code.lower()}@example.com',
            )
            self.systems[code] = system
            self.groups[code] = group

        self.motif = Motif.objects.create(
            code_motif='VILLE_RETRY',
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
            code_envoi='RETRY-001',
            systeme_ecart=self.systems['SIBO'],
            empreinte_anomalie='r' * 64,
        )
        self.prediction = PredictionMotif.objects.create(
            anomalie=self.anomaly,
            source_prediction=PredictionMotif.Source.APPRENTISSAGE,
            motif=self.motif,
            systeme_a_corriger_predit=self.systems['SIBO'],
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
        self.validation = ValidationMotif.objects.create(
            anomalie=self.anomaly,
            prediction_retenue=self.prediction,
            motif_final=self.motif,
            systeme_a_corriger_final=self.systems['SIBO'],
            superviseur=self.user,
            decision=ValidationMotif.Decision.ACCEPTE,
        )

    def _failed_notification(self, system_code):
        return Notification.objects.create(
            validation=self.validation,
            groupe=self.groups[system_code],
            objet=f'Failed {system_code}',
            message='historique',
            statut=Notification.Statut.ECHEC,
            nb_tentatives=1,
            erreur='SMTP temporairement indisponible',
        )

    def test_retry_only_resends_the_failed_current_target(self):
        failed = self._failed_notification('SICOM')

        retried = retry_failed_notification(failed)

        self.assertEqual(retried.statut, Notification.Statut.ENVOYEE)
        self.assertEqual(retried.groupe.systeme.code_systeme, 'SICOM')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sicom@example.com'])
        self.assertFalse(
            Notification.objects.filter(
                pk__gt=failed.pk,
                groupe__systeme=self.systems['SMI'],
            ).exists()
        )
        failed.refresh_from_db()
        self.assertEqual(failed.statut, Notification.Statut.ECHEC)

    def test_obsolete_sibo_failure_is_not_retried(self):
        failed = self._failed_notification('SIBO')

        with self.assertRaises(RoutingError) as ctx:
            retry_failed_notification(failed)

        self.assertIn('obsolète', str(ctx.exception))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.count(), 1)
