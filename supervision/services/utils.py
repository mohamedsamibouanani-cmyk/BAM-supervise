import hashlib
import math
import re
from decimal import Decimal, InvalidOperation

EMPTY_MARKERS = {'', 'nan', 'none', 'null', 'nat', '_x001a_', '_x001a__x001a__x001a_'}


def clean_text(value):
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    text = str(value).strip()
    if text.lower() in EMPTY_MARKERS:
        return ''
    return re.sub(r'\s+', ' ', text)


def normalize_value(value, type_value='TEXTE'):
    text = clean_text(value)
    if not text:
        return ''
    if type_value == 'NOMBRE':
        candidate = text.replace('\u00a0', '').replace(' ', '').replace(',', '.')
        try:
            return format(Decimal(candidate), 'f')
        except InvalidOperation:
            return text.upper()
    if type_value == 'BOOLEEN':
        val = text.lower()
        if val in {'1', 'true', 'oui', 'yes', 'o'}:
            return '1'
        if val in {'0', 'false', 'non', 'no', 'n'}:
            return '0'
    return text.upper()


def stable_hash(*parts):
    payload = '|'.join(clean_text(p) for p in parts)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def sha256_bytes(data: bytes):
    return hashlib.sha256(data).hexdigest()


def infer_attribute_type(column):
    c = column.upper()
    numeric_tokens = ('MNT', 'MONTANT', 'CRBT', 'TVA', 'TTC', 'HT', 'POIDS', 'VALEUR_DECLAREE', 'VD', 'QTE', 'QUANTITE')
    date_tokens = ('DATE',)
    if any(token in c for token in numeric_tokens):
        return 'NOMBRE'
    if any(token in c for token in date_tokens):
        return 'DATE'
    return 'TEXTE'


def infer_attribute_scope(column):
    c = column.upper()
    service_tokens = ('CRBT', 'VALEUR_DECLAREE', 'VD', 'SMS', 'NOTIF', 'MNT_', 'TVA', 'TTC', 'MONTANT')
    return 'SERVICE' if any(token in c for token in service_tokens) else 'ENVOI'
