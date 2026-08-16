from __future__ import annotations

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
    """Qualification lisible d'une vraie probabilité issue du modèle ML.

    Le pourcentage original reste la seule valeur numérique affichable. Cette
    qualification n'altère pas la probabilité et sert uniquement à éviter qu'un
    score faible soit lu comme une certitude métier.
    """
    value = float(probability)
    if value >= 0.70:
        return 'ELEVEE'
    if value >= 0.50:
        return 'MODEREE'
    return 'FAIBLE'
