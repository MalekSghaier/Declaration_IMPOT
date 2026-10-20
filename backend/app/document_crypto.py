"""Chiffrement applicatif des copies de documents : AES-256-GCM.

Format du blob (identique sur disque et dans MinIO) :
    MAGIC(4) | VERSION(1) | KEY_ID_LEN(1) | KEY_ID | NONCE(12) | CIPHERTEXT + TAG(16)

L'en-tete et le contexte (societe, document, version) sont authentifies (AAD) :
un blob modifie, ou copie vers un autre document, ne se dechiffre pas.
"""
import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

SCHEME = "AES-256-GCM-v1"

_MAGIC = b"DAE1"
_FORMAT_VERSION = 1
_NONCE_SIZE = 12
_TAG_SIZE = 16
_FIXED_HEADER_SIZE = len(_MAGIC) + 2  # MAGIC + VERSION + KEY_ID_LEN


class CryptoError(Exception):
    """Erreur de chiffrement / dechiffrement.

    Codes :
      EMPTY_PAYLOAD   : contenu vide
      INVALID_HEADER  : blob trop court, magic ou version inconnus, en-tete incoherent
      UNKNOWN_KEY_ID  : l'identifiant de cle du blob ne correspond a aucune cle connue
      DECRYPT_FAILED  : authentification echouee (blob modifie, mauvais contexte, mauvaise cle)
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code} : {message}")
        self.code = code


def build_aad(company_id: int, document_id: int, version: int) -> bytes:
    """Contexte authentifie : lie le blob a sa societe, son document et sa version."""
    return f"decl-doc|v1|{company_id}|{document_id}|{version}".encode("ascii")


def _current_key() -> tuple[str, bytes]:
    key = base64.b64decode(settings.DOCUMENT_ENCRYPTION_KEY.get_secret_value())
    return settings.DOCUMENT_ENCRYPTION_KEY_ID, key


def _key_for(key_id: str) -> bytes:
    current_id, key = _current_key()
    if key_id != current_id:
        raise CryptoError("UNKNOWN_KEY_ID", f"cle inconnue : {key_id!r}")
    return key


def encrypt_bytes(plaintext: bytes, *, aad: bytes) -> bytes:
    if not plaintext:
        raise CryptoError("EMPTY_PAYLOAD", "contenu vide")
    key_id, key = _current_key()
    key_id_bytes = key_id.encode("ascii")
    nonce = os.urandom(_NONCE_SIZE)
    header = _MAGIC + bytes([_FORMAT_VERSION, len(key_id_bytes)]) + key_id_bytes + nonce
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header + aad)
    return header + ciphertext


def decrypt_bytes(blob: bytes, *, aad: bytes) -> bytes:
    if len(blob) < _FIXED_HEADER_SIZE:
        raise CryptoError("INVALID_HEADER", "blob trop court")
    if blob[: len(_MAGIC)] != _MAGIC:
        raise CryptoError("INVALID_HEADER", "magic inconnu")
    if blob[len(_MAGIC)] != _FORMAT_VERSION:
        raise CryptoError("INVALID_HEADER", f"version de format inconnue : {blob[len(_MAGIC)]}")

    key_id_len = blob[len(_MAGIC) + 1]
    header_size = _FIXED_HEADER_SIZE + key_id_len + _NONCE_SIZE
    if key_id_len == 0 or len(blob) < header_size + _TAG_SIZE:
        raise CryptoError("INVALID_HEADER", "en-tete incoherent ou blob tronque")

    try:
        key_id = blob[_FIXED_HEADER_SIZE : _FIXED_HEADER_SIZE + key_id_len].decode("ascii")
    except UnicodeDecodeError:
        raise CryptoError("INVALID_HEADER", "identifiant de cle illisible")

    key = _key_for(key_id)
    header = blob[:header_size]
    nonce = blob[header_size - _NONCE_SIZE : header_size]
    try:
        return AESGCM(key).decrypt(nonce, blob[header_size:], header + aad)
    except InvalidTag:
        raise CryptoError("DECRYPT_FAILED", "authentification echouee")