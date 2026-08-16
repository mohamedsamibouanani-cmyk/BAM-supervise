import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

_ENC_PREFIX = 'enc:v1:'
_FP_PREFIX = 'hmac:v1:'
_FILE_PREFIX = b'bamfile:v1:'


def _secret_bytes():
    return settings.BAM_DATA_ENCRYPTION_KEY.encode('utf-8')


def _fernet():
    digest = hashlib.sha256(_secret_bytes()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_sensitive(value):
    """Encrypt a sensitive plaintext for storage at rest."""
    if value in (None, ''):
        return value
    text = str(value)
    if text.startswith(_ENC_PREFIX):
        return text
    token = _fernet().encrypt(text.encode('utf-8')).decode('ascii')
    return f'{_ENC_PREFIX}{token}'


def decrypt_sensitive(value):
    """Decrypt a value produced by encrypt_sensitive; legacy plaintext is returned as-is."""
    if value in (None, ''):
        return value
    text = str(value)
    if not text.startswith(_ENC_PREFIX):
        return text
    try:
        return _fernet().decrypt(text[len(_ENC_PREFIX):].encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        raise ValueError('Impossible de déchiffrer une donnée sensible avec la clé configurée.') from exc


def sensitive_fingerprint(value):
    """Keyed deterministic fingerprint used only for equality comparison of sensitive values."""
    if value in (None, ''):
        return value
    text = str(value)
    digest = hmac.new(_secret_bytes(), text.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{_FP_PREFIX}{digest}'


def encrypt_file_bytes(data: bytes) -> bytes:
    """Encrypt an imported source file before it is persisted in the media volume."""
    if not data:
        return data
    if data.startswith(_FILE_PREFIX):
        return data
    return _FILE_PREFIX + _fernet().encrypt(data)


def decrypt_file_bytes(data: bytes) -> bytes:
    """Decrypt a stored BAM import file. Legacy plaintext bytes are returned unchanged."""
    if not data:
        return data
    if not data.startswith(_FILE_PREFIX):
        return data
    try:
        return _fernet().decrypt(data[len(_FILE_PREFIX):])
    except InvalidToken as exc:
        raise ValueError('Impossible de déchiffrer le fichier importé avec la clé configurée.') from exc


def masked_sensitive_value(value):
    return '••••••••' if value not in (None, '') else None
