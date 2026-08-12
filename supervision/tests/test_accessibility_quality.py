from django.test import TestCase
from django.urls import reverse

from supervision.models import Anomalie, CampagneSupervision, Systeme, Superviseur


class AccessibilityAndBrowserQualityTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='quality-supervisor',
            password='Strong-Test-Password-2026!',
        )
        self.smi = Systeme.objects.create(code_systeme='SMI', nom_systeme='SMI', ordre_comparaison=1)
        self.sicom = Systeme.objects.create(code_systeme='SICOM', nom_systeme='SICOM', ordre_comparaison=2)
        self.sibo = Systeme.objects.create(code_systeme='SIBO', nom_systeme='SIBO', ordre_comparaison=3)
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        self.anomaly = Anomalie.objects.create(
            campagne=campaign,
            niveau=Anomalie.Niveau.ENVOI,
            type_ecart=Anomalie.TypeEcart.ABSENT,
            code_envoi='QUALITY-001',
            systeme_ecart=self.sibo,
            statut=Anomalie.Statut.ANALYSEE,
            empreinte_anomalie='q' * 64,
        )
        self.client.force_login(self.user)

    def test_authenticated_pages_expose_skip_navigation_and_no_store(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="skip-link"')
        self.assertContains(response, 'href="#main-content"')
        self.assertContains(response, 'id="main-content"')
        self.assertContains(response, 'accessibility.css')
        self.assertEqual(response.headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('private', response.headers['Cache-Control'])

    def test_validation_javascript_is_external_and_csp_compatible(self):
        response = self.client.get(reverse('anomaly_validate', args=[self.anomaly.pk]))
        body = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertIn('/static/supervision/validation-form.js', body)
        self.assertNotIn('function initValidationPage()', body)
        self.assertIn('<fieldset class="validation-choice-fieldset">', body)
        self.assertIn('Décision du superviseur', body)
        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        self.assertNotIn("script-src 'self' 'unsafe-inline'", response.headers['Content-Security-Policy'])

    def test_icon_only_global_controls_have_accessible_names(self):
        response = self.client.get(reverse('anomaly_list'))

        self.assertContains(response, 'aria-label="Se déconnecter"')
        self.assertContains(response, 'aria-label="Ouvrir les notifications"')
        self.assertContains(response, 'aria-label="Ouvrir la navigation"')
