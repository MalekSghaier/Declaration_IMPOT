"""Règles de téléversement communes (onboarding et pièces) : formats, taille, contenu réel.

À placer dans app/upload_rules.py.
La taille maximale reste portée par settings.UPLOAD_MAX_FILE_BYTES (50 Mo = 52428800).
"""
import io
import mimetypes
import zipfile

from app.config import settings

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Garantit que mimetypes connaît .docx quel que soit l'OS (Windows : dépend du registre sinon)
mimetypes.add_type(DOCX_CONTENT_TYPE, ".docx")

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".docx"}
FORMATS_LABEL = "PDF, PNG, JPG ou DOCX"

_EXPECTED_TYPE = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": DOCX_CONTENT_TYPE,
}

# Garde-fou : taille décompressée maximale d'un .docx (c'est une archive ZIP)
MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


def max_size_label() -> str:
    """Ex. '50 Mo', pour les messages d'erreur."""
    return f"{settings.UPLOAD_MAX_FILE_BYTES // (1024 * 1024)} Mo"


def _is_docx(raw: bytes) -> bool:
    """Vrai .docx : archive ZIP contenant [Content_Types].xml et word/document.xml."""
    if not raw.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                return False
            return sum(i.file_size for i in infos) <= MAX_DOCX_UNCOMPRESSED_BYTES
    except (zipfile.BadZipFile, ValueError, OSError):
        return False


def detect_content_type(raw: bytes) -> str | None:
    """Type réel du fichier d'après son contenu (et non son nom)."""
    if raw.startswith(b"%PDF"):
        return "application/pdf"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if _is_docx(raw):
        return DOCX_CONTENT_TYPE
    return None


def extension_matches(ext: str, content_type: str) -> bool:
    """L'extension annoncée correspond au contenu réel (évite un .docx nommé .pdf)."""
    return _EXPECTED_TYPE.get(ext) == content_type