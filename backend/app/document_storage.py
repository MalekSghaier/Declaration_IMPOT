"""Replication physique d'un document : copie locale + copie MinIO, avec verification.

Une ligne DocumentFile contient les DEUX copies (local_path + minio_bucket/minio_key).
Le statut est unique pour la ligne : il passe a REPLICATED uniquement si les DEUX
copies ont ete ecrites ET verifiees (local_verified_sha256 == minio_verified_sha256
== content_sha256).

Regles (voir points valides) :
- SHA-256 du raw verifie en entree : doit correspondre a Document.sha256.
- Lease atomique (lease_expires_at) : empeche deux workers de repliquer en parallele.
- Une copie deja verifiee n'est JAMAIS reecrite.
- Si une seule copie est valide, le statut reste FAILED mais la copie valide est conservee.
- Apres MAX attempts, intervention manuelle requise (reset_for_retry).

"""

import hashlib
import logging
import mimetypes
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app import document_crypto, storage
from app.audit import log_action
from app.config import settings
from app.file_status import assert_transition
from app.models import Document, DocumentFile

logger = logging.getLogger("app.document_storage")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_VERSION = 1
_DEFAULT_EXT = ".bin"
_EXT_MAP = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
}


class StorageError(Exception):
    """Erreur de replication ou de lecture du stockage physique.

    Codes :
      SHA_MISMATCH          : sha256(raw) != Document.sha256
      INVALID_SHA256        : sha256 mal forme (pas [0-9a-f]{64})
      EMPTY_PAYLOAD         : raw vide
      MAX_ATTEMPTS_REACHED  : attempts >= MAX, reset manuel requis
      NOT_FAILED            : reset_for_retry() sur un statut != FAILED
      NOT_REPLICATED        : aucun DocumentFile courant
      COPY_NOT_READABLE     : aucune copie lisible (LOCAL + MINIO)
      VERSION_CONFLICT      : une autre requete a cree la meme version avec un contenu different
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code} : {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Utilitaires internes
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_sha256(value: str, field: str = "sha256") -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise StorageError("INVALID_SHA256", f"{field} mal forme : {value!r}")
    return value


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ext_from_content_type(content_type: str) -> str:
    if content_type in _EXT_MAP:
        return _EXT_MAP[content_type]
    guess = mimetypes.guess_extension(content_type or "")
    return guess or _DEFAULT_EXT


def _local_path(company_id: int, document_id: int, version: int, sha: str, ext: str) -> Path:
    root = Path(settings.DOCUMENTS_STORAGE_DIR).resolve()
    return root / str(company_id) / str(document_id) / f"v{version}" / f"{sha}{ext}"


def _minio_key(company_id: int, document_id: int, version: int, sha: str, ext: str) -> str:
    return f"{company_id}/documents/{document_id}/v{version}/{sha}{ext}"


def _write_local_atomic(path: Path, raw: bytes) -> None:
    """Ecrit raw dans path de facon atomique : fichier tmp unique + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _aad(df: DocumentFile) -> bytes:
    return document_crypto.build_aad(df.company_id, df.document_id, df.version)


def _decode_copy(df: DocumentFile, stored: bytes) -> bytes | None:
    """Octets stockes -> contenu en clair, ou None si illisible.

    encryption_scheme NULL = copie historique en clair.
    """
    if df.encryption_scheme is None:
        return stored
    if df.encryption_scheme != document_crypto.SCHEME:
        logger.error("document_file %s : schema de chiffrement inconnu %r", df.id, df.encryption_scheme)
        return None
    try:
        return document_crypto.decrypt_bytes(stored, aad=_aad(df))
    except document_crypto.CryptoError as exc:
        logger.warning("document_file %s : dechiffrement impossible (%s)", df.id, exc)
        return None


def _verify_local(df: DocumentFile, expected_sha: str) -> bool:
    path = Path(df.local_path)
    if not path.is_file():
        return False
    try:
        stored = path.read_bytes()
    except OSError:
        return False
    plain = _decode_copy(df, stored)
    return plain is not None and _compute_sha256(plain) == expected_sha


def _verify_minio(df: DocumentFile, expected_sha: str) -> bool:
    try:
        stored = storage.get_bytes(df.minio_key)
    except Exception:
        return False
    plain = _decode_copy(df, stored)
    return plain is not None and _compute_sha256(plain) == expected_sha


# ---------------------------------------------------------------------------
# Lease atomique
# ---------------------------------------------------------------------------

def _claim_lease(db: Session, df: DocumentFile) -> bool:
    """Tente d'acquerir le lease de replication. Retourne True si acquis.

    Reussit si :
      - df.status == 'PENDING'
      - df.status == 'REPLICATING' ET lease expire
      - df.status == 'FAILED' ET attempts < MAX

    Atomique : un seul UPDATE conditionnel, aucun autre worker ne peut passer.
    """
    max_attempts = settings.DOCUMENT_REPLICATION_MAX_ATTEMPTS
    lease_seconds = settings.DOCUMENT_REPLICATION_LEASE_SECONDS
    now = _utcnow()
    deadline = now + timedelta(seconds=lease_seconds)

    stmt = (
        update(DocumentFile)
        .where(
            DocumentFile.id == df.id,
            or_(
                DocumentFile.status == "PENDING",
                (DocumentFile.status == "REPLICATING") & (DocumentFile.lease_expires_at < now),
                (DocumentFile.status == "FAILED") & (DocumentFile.attempts < max_attempts),
            ),
        )
        .values(
            status="REPLICATING",
            lease_expires_at=deadline,
            attempts=DocumentFile.attempts + 1,
            updated_at=func.now(),
        )
    )
    result = db.execute(stmt.execution_options(synchronize_session=False))
    db.commit()
    if result.rowcount == 0:
        return False
    db.refresh(df)
    return True


# ---------------------------------------------------------------------------
# Replication
# ---------------------------------------------------------------------------

def _ensure_local_copy(df: DocumentFile, raw: bytes, expected_sha: str) -> bool:
    """Verifie la copie locale ; la reecrit (chiffree) si absente/corrompue. Ne touche pas si valide."""
    if _verify_local(df, expected_sha):
        return True
    path = Path(df.local_path)
    try:
        _write_local_atomic(path, document_crypto.encrypt_bytes(raw, aad=_aad(df)))
    except Exception:
        logger.exception("ecriture locale impossible : %s", path)
        return False
    return _verify_local(df, expected_sha)


def _ensure_minio_copy(df: DocumentFile, raw: bytes, content_type: str, expected_sha: str) -> bool:
    """Verifie l'objet MinIO ; le reecrit (chiffre) si absent/corrompu. Ne touche pas si valide."""
    if _verify_minio(df, expected_sha):
        return True
    try:
        # Le blob est chiffre : le type MIME d'origine n'est pas annonce a MinIO
        storage.upload_bytes(df.minio_key, document_crypto.encrypt_bytes(raw, aad=_aad(df)), "application/octet-stream")
    except Exception:
        logger.exception("ecriture MinIO impossible : %s", df.minio_key)
        return False
    return _verify_minio(df, expected_sha)


def _mark_failed(db: Session, df: DocumentFile, reason: str) -> None:
    """Passe df a FAILED. Les champs de verification deja renseignes sont conserves."""
    if df.status == "REPLICATING":
        assert_transition("REPLICATING", "FAILED")
    df.status = "FAILED"
    df.lease_expires_at = None
    df.updated_at = _utcnow()
    db.commit()
    logger.warning("document_file %s : FAILED (%s)", df.id, reason)


def _do_replicate(db: Session, df: DocumentFile, raw: bytes, content_type: str) -> None:
    """Replication sous lease. Le statut de df est REPLICATING a l'entree.

    Les deux backends sont tentes INDEPENDAMMENT : si LOCAL echoue, on essaie
    quand meme MINIO, et vice-versa. Le statut final depend de l'etat des deux.
    """
    expected_sha = df.content_sha256
    if df.encryption_scheme != document_crypto.SCHEME:
        # Ligne pas encore repliquee (PENDING/FAILED) creee avant le chiffrement :
        # les verifications faites sur des copies en clair ne sont plus valables,
        # les deux copies seront reecrites chiffrees.
        df.encryption_scheme = document_crypto.SCHEME
        df.local_verified_sha256 = None
        df.local_verified_at = None
        df.minio_verified_sha256 = None
        df.minio_verified_at = None
        db.commit()
    # Les deux copies sont TOUJOURS reverifiees : une copie deja marquee verifiee a pu
    # disparaitre ou changer depuis la tentative precedente. Une copie valide n'est pas reecrite.
    # --- LOCAL ---
    local_ok = _ensure_local_copy(df, raw, expected_sha)
    df.local_verified_sha256 = expected_sha if local_ok else None
    df.local_verified_at = _utcnow() if local_ok else None
    db.commit()

    # --- MINIO (toujours tente, meme si LOCAL a echoue) ---
    minio_ok = _ensure_minio_copy(df, raw, content_type, expected_sha)
    df.minio_verified_sha256 = expected_sha if minio_ok else None
    df.minio_verified_at = _utcnow() if minio_ok else None
    db.commit()

    # --- Decision finale ---
    if local_ok and minio_ok:
        assert_transition(df.status, "REPLICATED")
        df.status = "REPLICATED"
        df.replicated_at = _utcnow()
        df.lease_expires_at = None
        df.updated_at = _utcnow()

        document = db.get(Document, df.document_id)
        if document is not None:
            document.minio_key = df.minio_key

        db.commit()
    else:
        _mark_failed(db, df, "copie locale et/ou MinIO echouee")


def replicate(
    db: Session,
    document: Document,
    raw: bytes,
    content_type: str,
    *,
    version: int = _DEFAULT_VERSION,
) -> DocumentFile:
    """Replique un document (LOCAL + MINIO) sous lease.

    Idempotent : un second appel sur une version REPLICATED retourne la ligne sans rien faire.

    Cas d'echec :
      - SHA-256(raw) != document.sha256 -> StorageError('SHA_MISMATCH')
      - attempts >= MAX et FAILED       -> StorageError('MAX_ATTEMPTS_REACHED')
      - un autre worker detient le lease -> retourne la ligne en REPLICATING
      - copie locale/MinIO en echec      -> retourne la ligne en FAILED (pas d'exception)

    Ne modifie pas Document.minio_key ni ne declenche l'OCR si les deux copies ne sont
    pas verifiees.
    """
    if not raw:
        raise StorageError("EMPTY_PAYLOAD", "contenu vide")
    expected_sha = _validate_sha256(document.sha256, "document.sha256")
    actual_sha = _compute_sha256(raw)
    if actual_sha != expected_sha:
        raise StorageError(
            "SHA_MISMATCH",
            f"sha256(raw)={actual_sha} != document.sha256={expected_sha}",
        )

    df = db.scalar(
        select(DocumentFile).where(
            DocumentFile.document_id == document.id,
            DocumentFile.version == version,
        )
    )

    if df is None:
        ext = _ext_from_content_type(content_type)
        df = DocumentFile(
            company_id=document.company_id,
            document_id=document.id,
            version=version,
            is_current=True,
            original_filename=document.filename or "document",
            content_type=content_type or "application/octet-stream",
            size_bytes=len(raw),
            content_sha256=expected_sha,
            checksum_computed_at=_utcnow(),
            local_path=str(_local_path(document.company_id, document.id, version, expected_sha, ext)),
            minio_bucket=settings.MINIO_BUCKET,
            minio_key=_minio_key(document.company_id, document.id, version, expected_sha, ext),
            encryption_scheme=document_crypto.SCHEME,
            status="PENDING",
            attempts=0,
        )
        try:
            with db.begin_nested():
                db.add(df)
        except IntegrityError:
            # Une autre requete a cree la meme version entre notre lecture et notre insertion.
            # Le SAVEPOINT n'annule que cette insertion : les modifications deja faites
            # dans la transaction (ex. Document) sont conservees.
            df = db.scalar(
                select(DocumentFile).where(
                    DocumentFile.document_id == document.id,
                    DocumentFile.version == version,
                )
            )
            if df is None:
                raise  # l'erreur n'est pas ce conflit de version : on la laisse remonter
            if df.content_sha256 != expected_sha:
                raise StorageError(
                    "VERSION_CONFLICT",
                    f"document {document.id} v{version} : une version existe deja avec un contenu different",
                )
        else:
            db.commit()
            db.refresh(df)

    if df.status == "REPLICATED":
        return df

    if not _claim_lease(db, df):
        db.refresh(df)
        max_attempts = settings.DOCUMENT_REPLICATION_MAX_ATTEMPTS
        if df.status == "FAILED" and df.attempts >= max_attempts:
            raise StorageError(
                "MAX_ATTEMPTS_REACHED",
                f"document {document.id} : {df.attempts} tentatives, reset_for_retry() requis",
            )
        # Un autre worker detient le lease
        return df

    _do_replicate(db, df, raw, content_type)
    db.refresh(df)
    return df


def reset_for_retry(
    db: Session,
    document: Document,
    *,
    user_id: int | None = None,
    reason: str | None = None,
    version: int = _DEFAULT_VERSION,
) -> DocumentFile:
    """Reinitialise un DocumentFile FAILED pour permettre une nouvelle tentative.

    - Ne touche PAS aux copies physiques ni aux champs de verification
      (local_verified_sha256 / minio_verified_sha256 conserves).
    - Remet attempts a 0, status a PENDING, lease_expires_at a None.
    - Trace dans AuditLog si user_id fourni.

    Leve StorageError('NOT_FAILED') si le statut n'est pas FAILED.
    """
    df = db.scalar(
        select(DocumentFile).where(
            DocumentFile.document_id == document.id,
            DocumentFile.version == version,
        )
    )
    if df is None:
        raise StorageError("NOT_REPLICATED", f"aucun DocumentFile pour document {document.id}")
    if df.status != "FAILED":
        raise StorageError("NOT_FAILED", f"statut courant = {df.status!r}, attendu 'FAILED'")

    old_attempts = df.attempts
    assert_transition("FAILED", "PENDING")
    df.status = "PENDING"
    df.attempts = 0
    df.lease_expires_at = None
    df.updated_at = _utcnow()

    if user_id is not None:
        log_action(
            db, document.company_id, user_id, "REPLICATION_RESET", "document", document.id,
            field="attempts", old=old_attempts, new=0, reason=reason,
        )
    db.commit()
    db.refresh(df)
    return df


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def _current_file(db: Session, document: Document) -> DocumentFile | None:
    return db.scalar(
        select(DocumentFile).where(
            DocumentFile.document_id == document.id,
            DocumentFile.is_current.is_(True),
        )
    )


def get_current_location(db: Session, document: Document) -> tuple[str, str] | None:
    """Retourne (bucket, key) de la copie MinIO verifiee, ou None.

    Priorite : DocumentFile courant REPLICATED. Sinon fallback sur Document.minio_key
    (documents historiques sans DocumentFile).
    """
    df = _current_file(db, document)
    if (
        df is not None
        and df.status == "REPLICATED"
        and df.minio_verified_sha256 == df.content_sha256
    ):
        return df.minio_bucket, df.minio_key
    if document.minio_key:
        return settings.MINIO_BUCKET, document.minio_key
    return None


def is_fully_replicated(db: Session, document: Document) -> bool:
    """True si la version courante est REPLICATED avec les DEUX SHA-256 concordants."""
    df = _current_file(db, document)
    if df is None:
        return False
    return (
        df.status == "REPLICATED"
        and df.local_verified_sha256 == df.content_sha256
        and df.minio_verified_sha256 == df.content_sha256
        and df.replicated_at is not None
    )


def read_bytes(db: Session, document: Document) -> bytes:
    """Lit les octets du document EN CLAIR. LOCAL en priorite (verifie), puis MINIO, puis
    fallback sur Document.minio_key pour les documents historiques sans DocumentFile.

    Le SHA-256 du contenu dechiffre est verifie apres chaque lecture. Leve
    StorageError('COPY_NOT_READABLE') si aucune source n'est valide.
    """
    expected_sha = document.sha256
    df = _current_file(db, document)

    if df is not None:
        # 1) copie locale
        path = Path(df.local_path)
        if path.is_file():
            try:
                plain = _decode_copy(df, path.read_bytes())
            except OSError:
                logger.exception("lecture locale impossible : %s", path)
                plain = None
            if plain is not None:
                if _compute_sha256(plain) == expected_sha:
                    return plain
                logger.warning("copie locale corrompue : %s", path)
        # 2) copie MinIO
        try:
            plain = _decode_copy(df, storage.get_bytes(df.minio_key))
            if plain is not None:
                if _compute_sha256(plain) == expected_sha:
                    return plain
                logger.warning("copie MinIO corrompue : %s", df.minio_key)
        except Exception:
            logger.exception("lecture MinIO echouee : %s", df.minio_key)

    # 3) fallback historique (document sans DocumentFile, copie en clair)
    if document.minio_key and (df is None or document.minio_key != df.minio_key):
        try:
            data = storage.get_bytes(document.minio_key)
            if _compute_sha256(data) == expected_sha:
                return data
        except Exception:
            logger.exception("lecture MinIO (fallback) echouee : %s", document.minio_key)

    raise StorageError("COPY_NOT_READABLE", f"document {document.id} : aucune copie valide")


def read_existing_copy(df: DocumentFile) -> bytes | None:
    """Contenu EN CLAIR d'une copie existante (LOCAL puis MINIO), ou None si aucune n'est lisible.

    Ne verifie PAS le SHA-256 : c'est l'appelant qui le fait.
    """
    if df.local_path:
        path = Path(df.local_path)
        if path.is_file():
            try:
                plain = _decode_copy(df, path.read_bytes())
            except OSError:
                logger.warning("lecture locale echouee : %s", path)
                plain = None
            if plain is not None:
                return plain
    if df.minio_key:
        try:
            plain = _decode_copy(df, storage.get_bytes(df.minio_key))
        except Exception:
            logger.warning("lecture MinIO echouee : %s", df.minio_key)
            plain = None
        if plain is not None:
            return plain
    return None