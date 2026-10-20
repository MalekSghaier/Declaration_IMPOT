import base64
import binascii
import re
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Toutes les valeurs viennent du fichier .env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    DATABASE_URL: str

    REDIS_URL: str

    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: SecretStr
    MINIO_BUCKET: str
    # True : connexion MinIO en HTTPS (obligatoire en production, MinIO doit alors avoir un certificat TLS)
    MINIO_SECURE: bool

    # Repertoire racine du stockage physique local des documents
    # (copie locale en plus de MinIO). Resolu en chemin absolu au premier usage.
    DOCUMENTS_STORAGE_DIR: str 

    DOCUMENT_REPLICATION_LEASE_SECONDS: int

    DOCUMENT_REPLICATION_MAX_ATTEMPTS: int
    DOCUMENT_ENCRYPTION_KEY: SecretStr
    DOCUMENT_ENCRYPTION_KEY_ID: str

    @field_validator("DOCUMENT_ENCRYPTION_KEY")
    @classmethod
    def _check_encryption_key(cls, v: SecretStr) -> SecretStr:
        try:
            key = base64.b64decode(v.get_secret_value(), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("DOCUMENT_ENCRYPTION_KEY doit etre du base64 valide")
        if len(key) != 32:
            raise ValueError("DOCUMENT_ENCRYPTION_KEY doit faire exactement 32 octets (AES-256)")
        return v

    @field_validator("DOCUMENT_ENCRYPTION_KEY_ID")
    @classmethod
    def _check_encryption_key_id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", v):
            raise ValueError("DOCUMENT_ENCRYPTION_KEY_ID : 1 a 32 caracteres [A-Za-z0-9_-]")
        return v

    MISTRAL_API_KEY: SecretStr
    MISTRAL_BASE_URL: str
    MISTRAL_OCR_MODEL: str
    MISTRAL_LLM_MODEL: str
    # Delai minimum (secondes) entre deux appels OCR, pour menager la limite de debit
    MISTRAL_OCR_MIN_INTERVAL: float

    # True : pas d'appel Mistral, l'extraction renvoie des donnees fictives (dev sans cle)
    OCR_MOCK: bool = False

    # Depot des factures et fiches de paie
    UPLOAD_MAX_FILE_BYTES: int
    UPLOAD_MAX_FILES_PER_REQUEST: int

    # Traitement automatique des pieces (Celery)
    PIECES_MAX_RETRIES: int
    PIECES_RETRY_BASE_SECONDS: int
    PIECES_RATE_LIMIT: str

    JWT_SECRET: SecretStr
    ACCESS_TOKEN_MINUTES: int
    REFRESH_TOKEN_DAYS: int
    COOKIE_SECURE: bool


settings = Settings()  # type: ignore[call-arg]