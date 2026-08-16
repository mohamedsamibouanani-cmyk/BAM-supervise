from django.test import TestCase

from supervision.models import (
    Anomalie, CampagneImport, CampagneSupervision, EnvoiSnapshot,
    FichierImport, Systeme, Superviseur, VerificationResolution,
)
from supervision.services.comparison import run_campaign
from supervision.services.utils import stable_hash


class ResolutionRecontrolScopeTests(TestCase):
    def setUp(self):
        self.user = Superviseur.objects.create_user(
            username='resolution-scope', password='Strong-Test-Password-2026!'
        )
        self.systems = {
            code: Systeme.objects.create(
                code_systeme=code, nom_systeme=code, ordre_comparaison=index
            )
            for index, code in enumerate(('SMI', 'SICOM', 'SIBO'), start=1)
        }
        self.file_counter = 0

    def _file(self, system_code, campaign_tag):
        self.file_counter += 1
        return FichierImport.objects.create(
            systeme=self.systems[system_code],
            superviseur=self.user,
            nom_fichier=f'{campaign_tag}_{system_code}.xlsx',
            chemin_stockage=f'/tmp/{campaign_tag}_{system_code}.xlsx',
            checksum_sha256=stable_hash(campaign_tag, system_code, self.file_counter),
            statut_import=FichierImport.Statut.CHARGE,
            nb_lignes_source=1,
            nb_lignes_retenues=1,
        )

    def _campaign(self, tag, shipment_presence):
        campaign = CampagneSupervision.objects.create(superviseur=self.user)
        files = {}
        for code in ('SMI', 'SICOM', 'SIBO'):
            files[code] = self._file(code, tag)
            CampagneImport.objects.create(
                campagne=campaign,
                systeme=self.systems[code],
                fichier_import=files[code],
            )
            for shipment_code in shipment_presence.get(code, []):
                EnvoiSnapshot.objects.create(
                    fichier_import=files[code],
                    code_envoi=shipment_code,
                    num_commande=f'CMD-{shipment_code}',
                    org_commerciale='2000',
                    ligne_premiere=2,
                    empreinte_envoi=stable_hash(tag, code, shipment_code),
                )
        run_campaign(campaign)
        return campaign

    def test_1_same_gap_becomes_persistent(self):
        first = self._campaign(
            'C1',
            {
                'SMI': ['E-PERSIST'],
                'SICOM': ['E-PERSIST'],
                'SIBO': [],
            },
        )
        old = first.anomalies.get(code_envoi='E-PERSIST', niveau=Anomalie.Niveau.ENVOI)
        old.statut = Anomalie.Statut.VALIDEE
        old.save(update_fields=['statut'])

        second = self._campaign(
            'C2',
            {
                'SMI': ['E-PERSIST'],
                'SICOM': ['E-PERSIST'],
                'SIBO': [],
            },
        )

        old.refresh_from_db()
        check = VerificationResolution.objects.get(anomalie=old, campagne_controle=second)
        self.assertEqual(check.resultat, VerificationResolution.Resultat.PERSISTANTE)
        self.assertEqual(old.statut, Anomalie.Statut.PERSISTANTE)
        self.assertIsNone(old.cloturee_le)

    def test_2_corrected_gap_becomes_resolved(self):
        first = self._campaign(
            'C1-RES',
            {
                'SMI': ['E-RESOLVED'],
                'SICOM': ['E-RESOLVED'],
                'SIBO': [],
            },
        )
        old = first.anomalies.get(code_envoi='E-RESOLVED', niveau=Anomalie.Niveau.ENVOI)
        old.statut = Anomalie.Statut.NOTIFIEE
        old.save(update_fields=['statut'])

        second = self._campaign(
            'C2-RES',
            {
                'SMI': ['E-RESOLVED'],
                'SICOM': ['E-RESOLVED'],
                'SIBO': ['E-RESOLVED'],
            },
        )

        old.refresh_from_db()
        check = VerificationResolution.objects.get(anomalie=old, campagne_controle=second)
        self.assertEqual(check.resultat, VerificationResolution.Resultat.RESOLUE)
        self.assertEqual(old.statut, Anomalie.Statut.RESOLUE)
        self.assertIsNotNone(old.cloturee_le)

    def test_3_unrelated_campaign_is_indeterminable_not_resolved(self):
        first = self._campaign(
            'C1-OTHER',
            {
                'SMI': ['E-OLD'],
                'SICOM': ['E-OLD'],
                'SIBO': [],
            },
        )
        old = first.anomalies.get(code_envoi='E-OLD', niveau=Anomalie.Niveau.ENVOI)
        old.statut = Anomalie.Statut.NOTIFIEE
        old.save(update_fields=['statut'])

        second = self._campaign(
            'C2-OTHER',
            {
                'SMI': ['E-NEW'],
                'SICOM': ['E-NEW'],
                'SIBO': ['E-NEW'],
            },
        )

        old.refresh_from_db()
        check = VerificationResolution.objects.get(anomalie=old, campagne_controle=second)
        self.assertEqual(check.resultat, VerificationResolution.Resultat.INDETERMINABLE)
        self.assertEqual(old.statut, Anomalie.Statut.NOTIFIEE)
        self.assertIsNone(old.cloturee_le)
