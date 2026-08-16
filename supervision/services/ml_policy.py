from __future__ import annotations

# Gouvernance ML : une cause n'est exploitable qu'après au moins deux validations
# humaines concordantes. Cela évite qu'un exemple isolé soit présenté comme une
# connaissance apprise suffisamment étayée.
MIN_CAUSE_SUPPORT = 2

# Motifs qui décrivent le constat déjà produit par le moteur déterministe.
# Ils appartiennent à l'historique métier mais ne sont ni des causes à apprendre,
# ni des suggestions que le ML doit reproposer au superviseur.
NON_CAUSAL_MOTIF_CODES = frozenset({
    'ATTRIBUT_NON_SYNCHRONISE',
    'ATTRIBUT_DIFFERENT',
    'SERVICE_ABSENT',
    'MOTIF_INCONNU',
})


def confidence_band(probability: float) -> str:
    """Qualification lisible d'une vraie probabilité issue du modèle ML."""
    value = float(probability)
    if value >= 0.70:
        return 'ELEVEE'
    if value >= 0.50:
        return 'MODEREE'
    return 'FAIBLE'
