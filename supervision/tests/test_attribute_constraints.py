from django.db import IntegrityError, transaction
from django.test import TestCase

from supervision.models import (
    AttributDefinition,
    EnvoiSnapshot,
    FichierImport,
    ServiceSnapshot,
    Systeme,
    Superviseur,
    ValeurAttributSnapshot,
)


class AttributeSnapshotConstraintTests(TestCase):
    def setUp(self):
        supervisor = Superviseur.objects.create_user(
            username='constraint-supervisor',
            password='Strong-Test-Password-2026!',
        )
        system = Systeme.objects.create(
            code_systeme='SMI',
            nom_systeme='SMI',
            ordre_comparaison=1,
        )
        imported_file = FichierImport.objects.create(
            systeme=system,
            superviseur=supervisor,
            nom_fichier='SMI.xlsx',
            chemin_stockage='/tmp/SMI.xlsx',
            checksum_sha256='a' * 64,
            statut_import=FichierImport.Statut.CHARGE,
        )
        self.shipments = [
            EnvoiSnapshot.objects.create(
                fichier_import=imported_file,
                code_envoi=f'E00{index}',
                ligne_premiere=index + 1,
                empreinte_envoi=str(index) * 64,
            )
            for index in (1, 2)
        ]
        self.services = [
            ServiceSnapshot.objects.create(
                envoi_snapshot=shipment,
                code_service='CRBT',
                ligne_source=index + 1,
                empreinte_service=str(index + 2) * 64,
            )
            for index, shipment in enumerate(self.shipments)
        ]
        self.shipment_attribute = AttributDefinition.objects.create(
            code_attribut='VILLE',
            libelle='Ville',
            portee=AttributDefinition.Portee.ENVOI,
            type_valeur=AttributDefinition.TypeValeur.TEXTE,
        )
        self.service_attribute = AttributDefinition.objects.create(
            code_attribut='CRBT',
            libelle='Contre remboursement',
            portee=AttributDefinition.Portee.SERVICE,
            type_valeur=AttributDefinition.TypeValeur.NOMBRE,
        )

    def test_unique_constraints_are_portable_to_mysql(self):
        constraints = {
            constraint.name: constraint
            for constraint in ValeurAttributSnapshot._meta.constraints
        }

        self.assertIsNone(constraints['uq_envoi_attribut'].condition)
        self.assertIsNone(constraints['uq_service_attribut'].condition)

    def test_duplicate_shipment_attribute_is_rejected(self):
        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=self.shipments[0],
            attribut=self.shipment_attribute,
            valeur_normalisee='RABAT',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ValeurAttributSnapshot.objects.create(
                envoi_snapshot=self.shipments[0],
                attribut=self.shipment_attribute,
                valeur_normalisee='CASABLANCA',
            )

        ValeurAttributSnapshot.objects.create(
            envoi_snapshot=self.shipments[1],
            attribut=self.shipment_attribute,
            valeur_normalisee='CASABLANCA',
        )

    def test_duplicate_service_attribute_is_rejected(self):
        ValeurAttributSnapshot.objects.create(
            service_snapshot=self.services[0],
            attribut=self.service_attribute,
            valeur_normalisee='1000',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ValeurAttributSnapshot.objects.create(
                service_snapshot=self.services[0],
                attribut=self.service_attribute,
                valeur_normalisee='800',
            )

        ValeurAttributSnapshot.objects.create(
            service_snapshot=self.services[1],
            attribut=self.service_attribute,
            valeur_normalisee='800',
        )
