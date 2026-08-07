from django.test import TestCase
from django.urls import reverse
from supervision.models import GroupeResponsable, Systeme, Superviseur


class ContactViewTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(username='sup', password='secret12345')
        self.system = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.group = GroupeResponsable.objects.create(systeme=self.system, nom_groupe='Groupe SMI')
        self.client.login(username='sup', password='secret12345')

    def test_supervisor_can_add_collaborator_email(self):
        response = self.client.post(reverse('contact_add', args=[self.group.pk]), {
            'nom_complet': 'Collab SMI', 'email': 'smi@example.com', 'fonction': 'Gestion', 'actif': 'on'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.group.contacts.filter(email='smi@example.com').exists())
