from django.contrib.auth import authenticate
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from supervision.models import (
    Anomalie, CampagneSupervision, ContactGroupe, GroupeResponsable,
    JournalAudit, Systeme, Superviseur,
)


class ApplicationSecurityTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='superviseur', password='Strong-Test-Password-2026!')
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        self.group = GroupeResponsable.objects.create(systeme=self.smi, nom_groupe='Groupe SMI')

    def test_protected_pages_require_authentication(self):
        for name in (
            'dashboard', 'campaign_list', 'campaign_create', 'anomaly_list',
            'notification_list', 'group_list', 'activity_list',
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('/login/', response.url)

    def test_csrf_is_required_for_state_change(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse('contact_add', args=[self.group.pk]), {
            'nom_complet': 'Contact CSRF',
            'email': 'csrf@example.com',
            'fonction': 'Test',
            'actif': 'on',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ContactGroupe.objects.filter(email='csrf@example.com').exists())

    def test_csrf_is_required_for_collaborator_activation_change(self):
        contact = ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Contact protégé',
            email='protected@example.com',
            actif=True,
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse('contact_toggle', args=[contact.pk]))
        self.assertEqual(response.status_code, 403)
        contact.refresh_from_db()
        self.assertTrue(contact.actif)

    def test_template_autoescape_blocks_stored_xss(self):
        ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='<script>alert("xss")</script>',
            email='xss@example.com',
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('group_list'))
        body = response.content.decode('utf-8')
        self.assertNotIn('<script>alert("xss")</script>', body)
        self.assertIn('&lt;script&gt;', body)

    def test_search_payload_is_handled_by_orm_without_sql_injection(self):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        Anomalie.objects.create(
            campagne=campaign,
            niveau='ENVOI',
            type_ecart='ABSENT',
            code_envoi='SAFE001',
            systeme_ecart=self.sibo,
            empreinte_anomalie='a' * 64,
        )
        before = Anomalie.objects.count()
        self.client.force_login(self.user)
        response = self.client.get(reverse('anomaly_list'), {'q': "' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Anomalie.objects.count(), before)

    def test_security_headers_are_present(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
        self.assertIn('camera=()', response.headers['Permissions-Policy'])

    def test_collaborator_is_not_an_application_user(self):
        ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Collaborateur SMI',
            email='collaborateur@example.com',
        )
        self.assertEqual(Superviseur.objects.count(), 1)
        self.assertIsNone(authenticate(username='collaborateur@example.com', password='anything'))

    def test_new_supervisor_password_uses_argon2(self):
        self.assertTrue(self.user.password.startswith('argon2$'), self.user.password.split('$', 1)[0])

    def test_collaborator_update_is_audited_and_can_deactivate(self):
        contact = ContactGroupe.objects.create(
            groupe=self.group,
            nom_complet='Contact actif',
            email='active@example.com',
            actif=True,
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('contact_edit', args=[contact.pk]), {
            'nom_complet': 'Contact actif',
            'email': 'active@example.com',
            'fonction': 'Exploitation',
        })
        self.assertEqual(response.status_code, 302)
        contact.refresh_from_db()
        self.assertFalse(contact.actif)
        audit = JournalAudit.objects.filter(action='UPDATE', entite='contact_groupe', id_entite=contact.pk).latest('cree_le')
        self.assertTrue(audit.anciennes_valeurs['actif'])
        self.assertFalse(audit.nouvelles_valeurs['actif'])
