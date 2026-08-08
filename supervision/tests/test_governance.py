from django.test import TestCase
from django.urls import reverse

from supervision.models import ContactGroupe, GroupeResponsable, JournalAudit, Systeme, Superviseur


class GovernanceTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='superviseur-test',
            password='Strong-Test-Password-2026!',
            nom_complet='Superviseur Test',
        )
        self.system = Systeme.objects.create(
            code_systeme='SMI',
            nom_systeme='SMI',
            ordre_comparaison=1,
        )
        self.group = GroupeResponsable.objects.create(
            systeme=self.system,
            nom_groupe='Groupe SMI',
        )
        self.contact = ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Collaborateur SMI',
            email='collaborateur@example.ma',
            fonction='Support',
            actif=True,
        )

    def test_governance_pages_require_authentication(self):
        response = self.client.get(reverse('activity_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

        response = self.client.post(reverse('contact_toggle', args=[self.contact.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_contact_toggle_is_post_only_and_audited(self):
        self.client.force_login(self.user)
        url = reverse('contact_toggle', args=[self.contact.pk])

        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)

        self.contact.refresh_from_db()
        self.assertFalse(self.contact.actif)
        audit = JournalAudit.objects.get(entite='contact_groupe', id_entite=self.contact.pk)
        self.assertEqual(audit.action, 'DEACTIVATE')
        self.assertEqual(audit.anciennes_valeurs['actif'], True)
        self.assertEqual(audit.nouvelles_valeurs['actif'], False)

        self.client.post(url)
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.actif)
        self.assertTrue(
            JournalAudit.objects.filter(
                entite='contact_groupe',
                id_entite=self.contact.pk,
                action='ACTIVATE',
            ).exists()
        )

    def test_activity_log_is_visible_to_supervisor(self):
        JournalAudit.objects.create(
            superviseur=self.user,
            action='TEST',
            entite='contact_groupe',
            id_entite=self.contact.pk,
            nouvelles_valeurs={'actif': True},
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('activity_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Journal des activités')
        self.assertContains(response, 'contact_groupe')
        self.assertContains(response, 'TEST')
