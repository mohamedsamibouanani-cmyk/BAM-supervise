from django.core.management.base import BaseCommand
from django.db import transaction

from supervision.models import (
    AttributDefinition, GroupeResponsable, Motif, RegleAffectation, RegleMetier,
    Systeme,
)


class Command(BaseCommand):
    help = 'Initialise les référentiels minimaux de BAM Supervise.'

    @transaction.atomic
    def handle(self, *args, **options):
        systems = {}
        for order, code in enumerate(['SMI', 'SICOM', 'SIBO'], start=1):
            systems[code], _ = Systeme.objects.update_or_create(
                code_systeme=code,
                defaults={'nom_systeme': code, 'ordre_comparaison': order, 'actif': True},
            )
            GroupeResponsable.objects.get_or_create(
                systeme=systems[code], nom_groupe=f'Groupe {code}',
                defaults={'description': f'Collaborateurs responsables du système {code}.'},
            )

        # N3 porte métier uniquement sur trois attributs : CRBT, téléphone de
        # notification et valeur déclarée. Les autres champs restent disponibles
        # pour le diagnostic complet du niveau ENVOI (N1).
        attrs = {
            'MONTANT_CRBT': ('Montant CRBT', 'SERVICE', 'NOMBRE', False),
            'TELEPHONE_NOTIFICATION': ('Téléphone de notification', 'SERVICE', 'TEXTE', True),
            'MONTANT_VALEUR_DECLAREE': ('Montant valeur déclarée', 'SERVICE', 'NOMBRE', False),
            # Alias historiques conservés pour les anciens imports/référentiels.
            'CRBT': ('Montant CRBT', 'SERVICE', 'NOMBRE', False),
            'TELEPHONE': ('Téléphone destinataire', 'ENVOI', 'TEXTE', True),
            'VALEUR_DECLAREE': ('Valeur déclarée', 'SERVICE', 'NOMBRE', False),
            # Champs utiles au diagnostic N1, mais non comparés comme attributs N3.
            'VILLE': ('Ville de destination', 'ENVOI', 'TEXTE', False),
            'VILLE_DESTINATION': ('Ville de destination', 'ENVOI', 'TEXTE', False),
            'PAYS': ('Pays de destination', 'ENVOI', 'TEXTE', False),
            'PAYS_DESTINATION': ('Pays de destination', 'ENVOI', 'TEXTE', False),
            'CODE_DIVISION': ('Code division', 'ENVOI', 'TEXTE', False),
            'MNT_HT': ('Montant HT', 'SERVICE', 'NOMBRE', False),
            'MNT_TVA': ('Montant TVA', 'SERVICE', 'NOMBRE', False),
            'MNT_TTC': ('Montant TTC', 'SERVICE', 'NOMBRE', False),
        }
        attr_objs = {}
        for code, (label, scope, typ, sensitive) in attrs.items():
            attr_objs[code], _ = AttributDefinition.objects.update_or_create(
                code_attribut=code,
                defaults={'libelle': label, 'portee': scope, 'type_valeur': typ, 'sensible': sensitive, 'actif': True},
            )

        motif_data = [
            ('VILLE_MANQUANTE', 'Ville obligatoire manquante', 'ENVOI', 'DONNEE', 'VILLE'),
            ('TELEPHONE_MANQUANT', 'Téléphone obligatoire manquant', 'ENVOI', 'DONNEE', 'TELEPHONE'),
            ('CHAMP_OBLIGATOIRE_ENVOI_ABSENT', 'Champ obligatoire absent', 'ENVOI', 'DONNEE', ''),
            ('FORMAT_CHAMP_ENVOI_NON_RESPECTE', 'Format de champ non respecté', 'ENVOI', 'FORMAT', ''),
            ('FORMAT_TELEPHONE_INVALIDE', 'Format du téléphone invalide', 'ENVOI', 'FORMAT', 'TELEPHONE'),
            ('FORMAT_SOURCE_INCOMPATIBLE', 'Format source potentiellement incompatible', 'ENVOI', 'FORMAT', ''),
            ('FORMAT_MONTANT_INCOMPATIBLE', 'Format de montant incompatible', 'ATTRIBUT', 'FORMAT', 'MONTANT'),
            ('FORMAT_ATTRIBUT_INCOMPATIBLE', 'Format d’attribut potentiellement incompatible', 'ATTRIBUT', 'FORMAT', ''),
            ('FORMAT_TEXTE_INVALIDE', 'Format de texte invalide', 'ENVOI', 'FORMAT', ''),
            ('CHAMP_SOURCE_MANQUANT', 'Champ source manquant ou vide', 'ENVOI', 'DONNEE', ''),
            ('CHAMP_OBLIGATOIRE_VIDE', 'Champ obligatoire vide', 'ATTRIBUT', 'DONNEE', ''),
            ('ATTRIBUT_NON_SYNCHRONISE', 'Attribut non synchronisé', 'ATTRIBUT', 'SYNCHRONISATION', ''),
            ('ATTRIBUT_DIFFERENT', 'Valeur d’attribut non synchronisée', 'ATTRIBUT', 'SYNCHRONISATION', ''),
            ('SERVICE_ABSENT', 'Service absent / non synchronisé', 'SERVICE', 'SYSTEME', 'ARTICLE'),
            ('SERVICE_INCONNU', 'Service non reconnu', 'SERVICE', 'REGLE', 'ARTICLE'),
            ('MOTIF_INCONNU', 'Motif non identifiable', 'ENVOI', 'TECHNIQUE', ''),
        ]
        motifs = {}
        for code, label, level, category, field in motif_data:
            motifs[code], _ = Motif.objects.update_or_create(
                code_motif=code,
                defaults={
                    'libelle': label, 'niveau_applicable': level, 'categorie': category,
                    'champ_typique': field, 'actif': True,
                },
            )

        # Ces règles historiques restent disponibles pour les anciens dossiers,
        # mais le moteur N1 courant applique désormais un ordre strict :
        # obligatoire absent -> format -> motif non identifiable.
        for col in ('VILLE', 'VILLE_DESTINATION'):
            RegleMetier.objects.update_or_create(
                code_regle=f'ENVOI_VILLE_VIDE_{col}',
                defaults={
                    'attribut': attr_objs[col], 'motif_suggere': motifs['VILLE_MANQUANTE'],
                    'niveau_anomalie': 'ENVOI', 'type_controle': 'VIDE',
                    'expression_regle': {}, 'seuil_confiance': 0.95, 'priorite': 10, 'actif': True,
                },
            )
        RegleMetier.objects.update_or_create(
            code_regle='ATTRIBUT_VALEUR_DIFFERENTE',
            defaults={
                'attribut': None, 'motif_suggere': motifs['ATTRIBUT_DIFFERENT'],
                'niveau_anomalie': 'ATTRIBUT', 'type_controle': 'DIFFERENCE',
                'expression_regle': {'type_ecart': 'DIFFERENT'},
                'seuil_confiance': 0.95, 'priorite': 5, 'actif': True,
            },
        )
        RegleMetier.objects.update_or_create(
            code_regle='ENVOI_TELEPHONE_VIDE',
            defaults={
                'attribut': attr_objs['TELEPHONE'], 'motif_suggere': motifs['TELEPHONE_MANQUANT'],
                'niveau_anomalie': 'ENVOI', 'type_controle': 'VIDE',
                'expression_regle': {}, 'seuil_confiance': 0.90, 'priorite': 20, 'actif': True,
            },
        )
        RegleMetier.objects.update_or_create(
            code_regle='ENVOI_TELEPHONE_FORMAT',
            defaults={
                'attribut': attr_objs['TELEPHONE'], 'motif_suggere': motifs['FORMAT_TELEPHONE_INVALIDE'],
                'niveau_anomalie': 'ENVOI', 'type_controle': 'FORMAT_INCOMPATIBLE',
                'expression_regle': {}, 'seuil_confiance': 0.90, 'priorite': 25, 'actif': True,
            },
        )
        RegleMetier.objects.update_or_create(
            code_regle='ENVOI_STRUCTURE_NUM_COMMANDE',
            defaults={
                'attribut': None, 'motif_suggere': motifs['CHAMP_SOURCE_MANQUANT'],
                'niveau_anomalie': 'ENVOI', 'type_controle': 'STRUCTURE_SOURCE',
                'expression_regle': {'field': 'NUM_COMMANDE'}, 'seuil_confiance': 0.90,
                'priorite': 26, 'actif': True,
            },
        )
        for col in ('VILLE', 'VILLE_DESTINATION', 'PAYS', 'PAYS_DESTINATION'):
            RegleMetier.objects.update_or_create(
                code_regle=f'ENVOI_{col}_FORMAT',
                defaults={
                    'attribut': attr_objs[col], 'motif_suggere': motifs['FORMAT_TEXTE_INVALIDE'],
                    'niveau_anomalie': 'ENVOI', 'type_controle': 'FORMAT_INCOMPATIBLE',
                    'expression_regle': {}, 'seuil_confiance': 0.90, 'priorite': 30, 'actif': True,
                },
            )

        RegleMetier.objects.filter(niveau_anomalie='SERVICE').update(actif=False)
        RegleMetier.objects.filter(
            niveau_anomalie='ATTRIBUT', type_controle='VIDE'
        ).update(actif=False)

        for system in systems.values():
            group = GroupeResponsable.objects.get(systeme=system, nom_groupe=f'Groupe {system.code_systeme}')
            for motif in motifs.values():
                RegleAffectation.objects.get_or_create(
                    systeme_a_corriger=system,
                    motif=motif,
                    defaults={'groupe': group},
                )

        self.stdout.write(self.style.SUCCESS('Référentiels BAM initialisés.'))