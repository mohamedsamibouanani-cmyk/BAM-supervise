from django.test import TestCase
from django.urls import reverse

from supervision.models import GroupeResponsable, Systeme, Superviseur


class ContactViewTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='sup', password='secret12345')
        self.system = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.group = GroupeResponsable.objects.create(systeme=self.system, nom_groupe='Groupe SMI')
        self.client.login(username='sup', password='secret12345')

    def test_supervisor_can_add_collaborator_email_without_activity_status(self):
        response = self.client.post(reverse('contact_add', args=[self.group.pk]), {
            'nom_complet': 'Collab SMI',
            'email': 'smi@example.com',
            'fonction': 'Gestion',
        })
        self.assertEqual(response.status_code, 302)
        contact = self.group.contacts.get(email='smi@example.com')
        self.assertEqual(contact.nom_complet, 'Collab SMI')
        self.assertEqual(contact.fonction, 'Gestion')

    def test_contact_form_does_not_expose_active_inactive_field(self):
        response = self.client.get(reverse('contact_add', args=[self.group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="actif"')
        self.assertNotContains(response, 'Désactiver')
        self.assertNotContains(response, 'Réactiver')
