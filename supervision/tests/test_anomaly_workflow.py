from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from supervision.models import (
    Anomalie, CampagneSupervision, GroupeResponsable, Motif, Systeme, Superviseur,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_ALLOW_SIMULATED_DELIVERY=True,
)
class AnomalyWorkflowTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='workflow', password='secret12345')
        self.client.force_login(self.user)
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        self.group = GroupeResponsable.objects.create(
            systeme=self.sicom, nom_groupe='Groupe SICOM', email_collectif='sicom@example.com'
        )
        self.unknown = Motif.objects.create(
            code_motif='MOTIF_INCONNU', libelle='Motif non identifié',
            niveau_applicable='ENVOI', categorie='TECHNIQUE',
        )
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=campaign, niveau='ENVOI', type_ecart='ABSENT', code_envoi='E-404',
            systeme_ecart=self.sicom, empreinte_anomalie='f' * 64,
        )

    def test_supervisor_can_create_and_memorize_a_new_reason(self):
        response = self.client.post(reverse('anomaly_validate', args=[self.anomaly.pk]), {
            'nouveau_motif': 'Ville saisie uniquement avec des chiffres',
            'systeme_a_corriger_final': self.sicom.pk,
            'decision': 'MODIFIE',
            'commentaire': 'Corriger la ville source.',
        })

        self.assertRedirects(response, reverse('anomaly_detail', args=[self.anomaly.pk]))
        validation = self.anomaly.validations.get(est_finale=True)
        self.assertEqual(validation.motif_final.libelle, 'Ville saisie uniquement avec des chiffres')
        self.assertTrue(validation.exempleapprentissage.eligible)
        self.assertIn('Élément non synchronisé : Envoi E-404', mail.outbox[0].body)
        self.assertIn('Système où l’élément manque : SICOM', mail.outbox[0].body)
