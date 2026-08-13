from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from .utils import clean_text


MAX_PATTERN_LENGTH = 250
MAX_VALUE_LENGTH = 1000


def evaluate_validation_rule(raw_value, definition, specification):
    """Evaluate a configurable target-system format rule.

    The rule is deliberately data-driven so no SMI/SICOM/SIBO-specific behavior is
    hard-coded. Supported JSON keys are: regex, decimal_separator, integer_only,
    min_length, max_length, allowed_values, prefixes, date_formats, min_value and
    max_value. The function returns ``(None, '')`` when no usable rule is configured.
    """
    spec = specification or {}
    if not isinstance(spec, dict) or not spec:
        return None, ''

    text = clean_text(raw_value)
    if not text:
        return None, 'Valeur vide'
    text = text[:MAX_VALUE_LENGTH]

    expected_separator = spec.get('decimal_separator')
    if expected_separator in {'.', ','}:
        compact = text.replace('\u00a0', '').replace(' ', '')
        other = ',' if expected_separator == '.' else '.'
        if other in compact:
            return False, f'Séparateur décimal attendu : {expected_separator}'
        if expected_separator in compact:
            candidate = compact.replace(expected_separator, '.')
        else:
            candidate = compact
        try:
            Decimal(candidate)
        except (InvalidOperation, ValueError):
            return False, 'Valeur numérique invalide'

    if spec.get('integer_only'):
        compact = text.replace('\u00a0', '').replace(' ', '')
        if not re.fullmatch(r'[+-]?\d+', compact):
            return False, 'Valeur entière attendue'

    pattern = spec.get('regex') or spec.get('pattern')
    if pattern:
        pattern = str(pattern)
        if len(pattern) > MAX_PATTERN_LENGTH:
            return False, 'Règle de format trop longue'
        try:
            if re.fullmatch(pattern, text) is None:
                return False, spec.get('message') or 'Format attendu non respecté'
        except re.error:
            return False, 'Règle de format invalide'

    min_length = spec.get('min_length')
    max_length = spec.get('max_length')
    if min_length is not None and len(text) < int(min_length):
        return False, f'Longueur minimale attendue : {int(min_length)}'
    if max_length is not None and len(text) > int(max_length):
        return False, f'Longueur maximale attendue : {int(max_length)}'

    allowed = spec.get('allowed_values')
    if allowed:
        normalized_allowed = {str(value).strip().upper() for value in allowed}
        if text.upper() not in normalized_allowed:
            return False, 'Valeur hors domaine autorisé'

    prefixes = spec.get('prefixes')
    if prefixes and not any(text.startswith(str(prefix)) for prefix in prefixes):
        return False, 'Préfixe attendu non respecté'

    date_formats = spec.get('date_formats')
    if date_formats:
        matched = False
        for fmt in date_formats:
            try:
                datetime.strptime(text, str(fmt))
                matched = True
                break
            except (ValueError, TypeError):
                continue
        if not matched:
            return False, 'Format de date attendu non respecté'

    if definition.type_valeur == 'NOMBRE' and ('min_value' in spec or 'max_value' in spec):
        candidate = text.replace('\u00a0', '').replace(' ', '').replace(',', '.')
        try:
            number = Decimal(candidate)
        except (InvalidOperation, ValueError):
            return False, 'Valeur numérique invalide'
        if spec.get('min_value') is not None and number < Decimal(str(spec['min_value'])):
            return False, f'Valeur minimale attendue : {spec["min_value"]}'
        if spec.get('max_value') is not None and number > Decimal(str(spec['max_value'])):
            return False, f'Valeur maximale attendue : {spec["max_value"]}'

    return True, ''
