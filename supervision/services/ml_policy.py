from __future__ import annotations

# Gouvernance ML : une cause n'est exploitable qu'après au moins deux validations
# humaines concordantes. Cela évite qu'un exemple isolé soit présenté comme une
# connaissance apprise suffisamment étayée.
MIN_CAUSE_SUPPORT = 2

# Motifs qui décrivent l'écart ou un symptôme déjà observable dans les données.
# Ils restent utiles au diagnostic et à l'historique, mais ne doivent pas être
# appris comme causes ML. Le ML doit apprendre le POURQUOI, pas répéter le constat.
NON_CAUSAL_MOTIF_CODES = frozenset({
    'ATTRIBUT_NON_SYNCHRONISE',
    'ATTRIBUT_DIFFERENT',
    'SERVICE_ABSENT',
    'MOTIF_INCONNU',
    'CHAMP_OBLIGATOIRE_VIDE',
    'CHAMP_OBLIGATOIRE_ENVOI_ABSENT',
    'CHAMP_SOURCE_MANQUANT',
    'VILLE_MANQUANTE',
    'TELEPHONE_MANQUANT',
})


def confidence_band(probability: float) -> str:
    """Qualification lisible d'une vraie probabilité issue du modèle ML."""
    value = float(probability)
    if value >= 0.70:
        return 'ELEVEE'
    if value >= 0.50:
        return 'MODEREE'
    return 'FAIBLE'
