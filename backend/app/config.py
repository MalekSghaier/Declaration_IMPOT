from pathlib import Path

from pydantic import SecretStr
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

    MISTRAL_API_KEY: SecretStr
    MISTRAL_BASE_URL: str
    MISTRAL_OCR_MODEL: str
    MISTRAL_LLM_MODEL: str

    # True : pas d'appel Mistral, l'extraction renvoie des donnees fictives (dev sans cle)
    OCR_MOCK: bool = False

    JWT_SECRET: SecretStr
    ACCESS_TOKEN_MINUTES: int
    REFRESH_TOKEN_DAYS: int
    COOKIE_SECURE: bool


settings = Settings()  # type: ignore[call-arg]