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

        attrs = {
            'VILLE': ('Ville de destination', 'ENVOI', 'TEXTE', False),
            'VILLE_DESTINATION': ('Ville de destination', 'ENVOI', 'TEXTE', False),
            'PAYS': ('Pays de destination', 'ENVOI', 'TEXTE', False),
            'PAYS_DESTINATION': ('Pays de destination', 'ENVOI', 'TEXTE', False),
            'TELEPHONE': ('Téléphone destinataire', 'ENVOI', 'TEXTE', True),
            'CODE_DIVISION': ('Code division', 'ENVOI', 'TEXTE', False),
            'MNT_HT': ('Montant HT', 'SERVICE', 'NOMBRE', False),
            'MNT_TVA': ('Montant TVA', 'SERVICE', 'NOMBRE', False),
            'MNT_TTC': ('Montant TTC', 'SERVICE', 'NOMBRE', False),
            'CRBT': ('Montant CRBT', 'SERVICE', 'NOMBRE', False),
            'VALEUR_DECLAREE': ('Valeur déclarée', 'SERVICE', 'NOMBRE', False),
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
            ('FORMAT_TELEPHONE_INVALIDE', 'Format du téléphone invalide', 'ENVOI', 'FORMAT', 'TELEPHONE'),
            ('FORMAT_MONTANT_INCOMPATIBLE', 'Format de montant incompatible', 'ATTRIBUT', 'FORMAT', 'MONTANT'),
            ('FORMAT_TEXTE_INVALIDE', 'Format de texte invalide', 'ENVOI', 'FORMAT', ''),
            ('CHAMP_SOURCE_MANQUANT', 'Champ source obligatoire manquant', 'ENVOI', 'DONNEE', ''),
            ('CHAMP_OBLIGATOIRE_VIDE', 'Champ obligatoire vide', 'ATTRIBUT', 'DONNEE', ''),
            ('ATTRIBUT_DIFFERENT', 'Valeur d’attribut non synchronisée', 'ATTRIBUT', 'SYNCHRONISATION', ''),
            ('SERVICE_ABSENT', 'Service absent / non synchronisé', 'SERVICE', 'SYNCHRONISATION', 'ARTICLE'),
            ('SERVICE_INCONNU', 'Service non reconnu', 'SERVICE', 'REGLE', 'ARTICLE'),
            ('MOTIF_INCONNU', 'Motif non identifié', 'ENVOI', 'TECHNIQUE', ''),
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

        # Règles génériques : les flux exacts restent configurables dans l'administration.
        for level in ('ENVOI', 'ATTRIBUT'):
            for col in ('VILLE', 'VILLE_DESTINATION'):
                RegleMetier.objects.update_or_create(
                    code_regle=f'{level}_VILLE_VIDE_{col}',
                    defaults={
                        'attribut': attr_objs[col], 'motif_suggere': motifs['VILLE_MANQUANTE'],
                        'niveau_anomalie': level, 'type_controle': 'VIDE',
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
        # Seules les structures source réellement confirmées sont initialisées.
        # ARTICLE / DES_ARTICLE ne sont pas considérés comme invalides sans règle métier validée.
        RegleMetier.objects.update_or_create(
            code_regle='ENVOI_STRUCTURE_NUM_COMMANDE',
            defaults={
                'attribut': None, 'motif_suggere': motifs['CHAMP_SOURCE_MANQUANT'],
                'niveau_anomalie': 'ENVOI', 'type_controle': 'STRUCTURE_SOURCE',
                'expression_regle': {'field': 'NUM_COMMANDE'}, 'seuil_confiance': 0.90,
                'priorite': 26, 'actif': True,
            },
        )
        for level in ('ENVOI', 'SERVICE'):
            for col in ('VILLE', 'VILLE_DESTINATION', 'PAYS', 'PAYS_DESTINATION'):
                RegleMetier.objects.update_or_create(
                    code_regle=f'{level}_{col}_FORMAT',
                    defaults={
                        'attribut': attr_objs[col], 'motif_suggere': motifs['FORMAT_TEXTE_INVALIDE'],
                        'niveau_anomalie': level, 'type_controle': 'FORMAT_INCOMPATIBLE',
                        'expression_regle': {}, 'seuil_confiance': 0.90, 'priorite': 30, 'actif': True,
                    },
                )
            for col in ('MNT_HT', 'MNT_TVA', 'MNT_TTC', 'CRBT', 'VALEUR_DECLAREE'):
                RegleMetier.objects.update_or_create(
                    code_regle=f'{level}_{col}_FORMAT',
                    defaults={
                        'attribut': attr_objs[col], 'motif_suggere': motifs['FORMAT_MONTANT_INCOMPATIBLE'],
                        'niveau_anomalie': level, 'type_controle': 'FORMAT_INCOMPATIBLE',
                        'expression_regle': {}, 'seuil_confiance': 0.90, 'priorite': 40, 'actif': True,
                    },
                )

        # Dans le prototype, chaque système possède un groupe par défaut. La combinaison
        # motif + système reste matérialisée dans regle_affectation et peut être spécialisée plus tard.
        for system in systems.values():
            group = GroupeResponsable.objects.get(systeme=system, nom_groupe=f'Groupe {system.code_systeme}')
            for motif in motifs.values():
                RegleAffectation.objects.get_or_create(systeme_a_corriger=system, motif=motif, defaults={'groupe': group})

        self.stdout.write(self.style.SUCCESS('Référentiels BAM initialisés.'))
