import io

from minio import Minio

from app.config import settings

_client = Minio(
    settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY.get_secret_value(),
    secure=settings.MINIO_SECURE,
)


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    _client.put_object(
        settings.MINIO_BUCKET, key, io.BytesIO(data), length=len(data), content_type=content_type
    )


def get_bytes(key: str) -> bytes:
    response = _client.get_object(settings.MINIO_BUCKET, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()