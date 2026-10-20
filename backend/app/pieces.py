import hashlib
import io
import logging
import mimetypes
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import document_storage, storage
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Company, Document, TaxPeriod, User
from app.services.classification import classify_direction
from app.services.schemas import InvoiceExtraction, PayslipExtraction
from app.services.validation import validate_invoice, validate_payslip
from app.tasks import process_piece
from typing import Any, Literal, Union
from app.audit import diff_fields, log_action
from app.intervals import StructureError
from app.tax_period_service import assert_accepts_documents, get_or_create_month
from app.tax_periods import to_http
from app.snapshots import audit_payload, create_or_confirm_snapshot, withdraw_current
from app.upload_rules import ALLOWED_EXTENSIONS, FORMATS_LABEL, detect_content_type, extension_matches, max_size_label

logger = logging.getLogger("app.pieces")

router = APIRouter(prefix="/api/pieces", tags=["pieces"])

KINDS = ("FACTURE", "FICHE_PAIE")
PERIOD_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")  # AAAA-MM, chiffres ASCII uniquement
STATUSES = ("UPLOADED", "PROCESSING", "EXTRACTED", "NEEDS_REVIEW", "VALIDATED", "REJECTED", "FAILED")


class FileResult(BaseModel):
    filename: str
    status: Literal["accepted", "duplicate", "rejected"]
    document_id: int | None = None
    reason: str | None = None


class UploadOut(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    results: list[FileResult]


class PieceOut(BaseModel):
    id: int
    kind: str
    filename: str
    period: str | None
    direction: str | None
    status: str
    tax_period_id: int | None = None
    created_at: datetime


class PieceDetailOut(PieceOut):
    error: str | None
    extracted_data: dict | None
    is_fully_replicated: bool = False


class PieceListOut(BaseModel):
    total: int
    items: list[PieceOut]


class SummaryOut(BaseModel):
    total: int
    counts: dict[str, int]


class ReviewIn(BaseModel):
    fields: dict[str, Any]
    direction: Literal["VENTE", "ACHAT"] | None = None
    action: Literal["save", "validate", "reject"]
    reject_reason: str | None = None


def _to_piece_out(d: Document) -> dict:
    return {
        "id": d.id,
        "kind": d.kind,
        "filename": d.filename,
        "period": d.period,
        "direction": d.direction,
        "status": d.processing_status,
        "tax_period_id": d.tax_period_id,
        "created_at": d.created_at,
    }


def _find_existing(db: Session, company_id: int, sha256: str) -> int | None:
    return db.scalar(
        select(Document.id).where(
            Document.company_id == company_id,
            Document.sha256 == sha256,
            Document.kind.in_(KINDS),
        )
    )


def _enqueue(document_id: int) -> None:
    """Met le traitement en file. Si Redis est indisponible, la piece reste UPLOADED (a relancer)."""
    try:
        process_piece.delay(document_id)
    except Exception:
        logger.exception("mise en file impossible pour le document %s (reste UPLOADED)", document_id)


def _store_one(company_id: int, kind: str, period: str, tax_period_id: int, upload: UploadFile, db: Session) -> FileResult:
    name = Path(upload.filename or "sans_nom").name[:255]

    def rejected(reason: str) -> FileResult:
        return FileResult(filename=name, status="rejected", reason=reason)

    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return rejected(f"format non supporte ({FORMATS_LABEL})")

    raw = upload.file.read(settings.UPLOAD_MAX_FILE_BYTES + 1)
    if not raw:
        return rejected("fichier vide")
    if len(raw) > settings.UPLOAD_MAX_FILE_BYTES:
        return rejected(f"fichier trop volumineux ({max_size_label()} max)")

    content_type = detect_content_type(raw)
    if content_type is None:
        return rejected(f"contenu non reconnu ({FORMATS_LABEL} attendus)")
    if not extension_matches(ext, content_type):
        return rejected("l'extension ne correspond pas au contenu du fichier")

    sha256 = hashlib.sha256(raw).hexdigest()

    existing = _find_existing(db, company_id, sha256)
    if existing is not None:
        return FileResult(filename=name, status="duplicate", document_id=existing, reason="fichier deja depose")

    # minio_key initialement vide : sera rempli par document_storage.replicate() en cas de succes
    doc = Document(
        company_id=company_id,
        kind=kind,
        minio_key="",
        filename=name,
        sha256=sha256,
        processing_status="UPLOADED",
        period=period,
        tax_period_id=tax_period_id,
    )
    db.add(doc)
    try:
        # Le flush materialise l'id et declenche les contraintes UNIQUE (company_id, sha256)
        # avant d'ecrire sur disque/MinIO : evite d'ecrire des copies inutiles pour un doublon.
        db.flush()
    except IntegrityError:
        # Depose en meme temps par une autre requete : l'index unique l'a bloque
        db.rollback()
        existing = _find_existing(db, company_id, sha256)
        return FileResult(filename=name, status="duplicate", document_id=existing, reason="fichier deja depose")

    # Replication LOCAL + MINIO avec verifications. Le statut final du DocumentFile
    # determine si l'on peut lancer l'OCR.
    try:
        df = document_storage.replicate(db, doc, raw, content_type)
    except document_storage.StorageError as exc:
        # Cas pathologiques (SHA_MISMATCH, EMPTY_PAYLOAD, MAX_ATTEMPTS_REACHED).
        # Le Document reste en base pour tracabilite et reprise ulterieure ; aucun OCR n'est lance.
        logger.warning("replication refusee pour %s : %s", name, exc)
        return FileResult(filename=name, status="accepted", document_id=doc.id, reason=str(exc))

    if df.status == "REPLICATED":
        # Les deux copies sont ecrites et verifiees : l'OCR peut demarrer.
        _enqueue(doc.id)
    else:
        # Replication incomplete : Document conserve en UPLOADED, repris manuellement plus tard.
        logger.warning(
            "document %s cree mais replication incomplete (status=%s, attempts=%s)",
            doc.id, df.status, df.attempts,
        )

    return FileResult(filename=name, status="accepted", document_id=doc.id)


@router.post("/upload", response_model=UploadOut)
def upload(
    kind: str = Form(...),
    period: str = Form(...),
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    kind = kind.upper()
    if kind not in KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind doit etre FACTURE ou FICHE_PAIE")
    if not PERIOD_RE.match(period):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "period doit etre au format AAAA-MM (ex. 2025-09)")
    if len(files) > settings.UPLOAD_MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"{settings.UPLOAD_MAX_FILES_PER_REQUEST} fichiers maximum par envoi",
        )

    company = db.get(Company, user.company_id)
    if company.status != "LOCKED":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Terminez d'abord l'onboarding de la societe")
    try:
        tax_period = get_or_create_month(db, user.company_id, period, user.id, reason="DEPOT_DOCUMENT")
        assert_accepts_documents(tax_period)
        tax_period_id = tax_period.id
        db.commit()
    except StructureError as exc:
        db.rollback()
        raise to_http(exc)

    results = [_store_one(user.company_id, kind, period, tax_period_id, f, db) for f in files]
    return UploadOut(
        accepted=sum(r.status == "accepted" for r in results),
        duplicates=sum(r.status == "duplicate" for r in results),
        rejected=sum(r.status == "rejected" for r in results),
        results=results,
    )


@router.get("/summary", response_model=SummaryOut)
def summary(
    period: str,
    kind: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not PERIOD_RE.match(period):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "period doit etre au format AAAA-MM")
    conditions = [
        Document.company_id == user.company_id,
        Document.kind.in_(KINDS),
        Document.period == period,
    ]
    if kind:
        if kind.upper() not in KINDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind doit etre FACTURE ou FICHE_PAIE")
        conditions.append(Document.kind == kind.upper())

    rows = db.execute(
        select(Document.processing_status, func.count()).where(*conditions).group_by(Document.processing_status)
    ).all()
    counts = {status_name: n for status_name, n in rows}
    return SummaryOut(total=sum(counts.values()), counts=counts)


@router.get("", response_model=PieceListOut)
def list_pieces(
    period: str | None = None,
    kind: str | None = None,
    status_: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conditions = [Document.company_id == user.company_id, Document.kind.in_(KINDS)]
    if period:
        if not PERIOD_RE.match(period):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "period doit etre au format AAAA-MM")
        conditions.append(Document.period == period)
    if kind:
        if kind.upper() not in KINDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind doit etre FACTURE ou FICHE_PAIE")
        conditions.append(Document.kind == kind.upper())
    if status_:
        if status_.upper() not in STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status doit etre l'un de {STATUSES}")
        conditions.append(Document.processing_status == status_.upper())

    total = db.scalar(select(func.count()).select_from(Document).where(*conditions))
    rows = db.scalars(
        select(Document).where(*conditions).order_by(Document.id.desc()).limit(limit).offset(offset)
    ).all()
    return PieceListOut(total=total or 0, items=[PieceOut(**_to_piece_out(d)) for d in rows])


@router.get("/{piece_id}", response_model=PieceDetailOut)
def get_piece(piece_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = db.scalar(
        select(Document).where(
            Document.id == piece_id,
            Document.company_id == user.company_id,
            Document.kind.in_(KINDS),
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Piece non trouvee")
    return PieceDetailOut(
        **_to_piece_out(doc),
        error=doc.error,
        extracted_data=doc.extracted_data,
        is_fully_replicated=document_storage.is_fully_replicated(db, doc),
    )


@router.get("/{piece_id}/file")
def get_file(piece_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = db.scalar(
        select(Document).where(
            Document.id == piece_id,
            Document.company_id == user.company_id,
            Document.kind.in_(KINDS),
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Piece non trouvee")
    try:
        raw = document_storage.read_bytes(db, doc)
    except document_storage.StorageError:
        logger.exception("lecture impossible pour le document %s", doc.id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Stockage indisponible")
    content_type = mimetypes.guess_type(doc.filename)[0] or "application/octet-stream"
    return StreamingResponse(io.BytesIO(raw), media_type=content_type)


@router.put("/{piece_id}/review", response_model=PieceDetailOut)
def review_piece(
    piece_id: int,
    body: ReviewIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.scalar(
        select(Document).where(
            Document.id == piece_id,
            Document.company_id == user.company_id,
            Document.kind.in_(KINDS),
        ).with_for_update()
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Piece non trouvee")
    if doc.processing_status in ("UPLOADED", "PROCESSING"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Traitement automatique pas encore termine")

    # Etat avant modification : sert de reference pour l'audit
    old_fields = (doc.extracted_data or {}).get("fields") or {}
    old_direction = doc.direction

    def audit_corrections(new_fields: dict) -> None:
        for field, old, new in diff_fields(old_fields, new_fields):
            log_action(
                db, doc.company_id, user.id, "PIECE_CORRIGEE", "document", doc.id,
                field=field, old=old, new=new,
            )

    # ---------- Rejet : commun aux deux types ----------
    if body.action == "reject":
        audit_corrections(body.fields)
        log_action(
            db, doc.company_id, user.id, "PIECE_REJETEE", "document", doc.id,
            reason=body.reject_reason,
        )
        withdraw_current(db, doc, user.id, "REOPENED_REJECT")
        doc.processing_status = "REJECTED"
        doc.extracted_data = {
            **(doc.extracted_data or {}),
            "fields": body.fields,
            "reject_reason": body.reject_reason,
        }
        db.commit()
        return PieceDetailOut(
            **_to_piece_out(doc),
            error=doc.error,
            extracted_data=doc.extracted_data,
            is_fully_replicated=document_storage.is_fully_replicated(db, doc),
        )

    # ---------- Validation typee du payload selon le kind ----------
    if doc.kind == "FACTURE":
        try:
            fields = InvoiceExtraction.model_validate(body.fields)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Champs de facture invalides")

        issues = validate_invoice(fields, doc.period)

        company = db.get(Company, doc.company_id)
        company_mf = company.matricule_fiscal if company else None
        if body.direction:
            direction = body.direction
            issues = [i for i in issues if i.field != "direction"]
        else:
            direction, class_issues = classify_direction(fields.emetteur_mf, fields.client_mf, company_mf)
            issues += class_issues

        if body.action == "validate":
            blocking = [i for i in issues if i.severity == "error"]
            if blocking:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{len(blocking)} erreur(s) bloquante(s) a corriger avant validation",
                )
            if direction is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choisissez vente ou achat avant validation")
            doc.processing_status = "VALIDATED"
        else:
            doc.processing_status = "NEEDS_REVIEW" if issues else "EXTRACTED"

        doc.direction = direction

    elif doc.kind == "FICHE_PAIE":
        try:
            fields = PayslipExtraction.model_validate(body.fields)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Champs de fiche de paie invalides")

        issues = validate_payslip(fields, doc.period)

        if body.action == "validate":
            blocking = [i for i in issues if i.severity == "error"]
            if blocking:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{len(blocking)} erreur(s) bloquante(s) a corriger avant validation",
                )
            doc.processing_status = "VALIDATED"
        else:
            doc.processing_status = "NEEDS_REVIEW" if issues else "EXTRACTED"

        doc.direction = None  # pas de notion vente/achat pour une fiche de paie

    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Type de piece non supporte : {doc.kind}")

    new_fields = fields.model_dump(mode="json")
    issues_payload = [asdict(i) for i in issues]
    method = (doc.extracted_data or {}).get("method")

    # ---------- Audit : ecrit seulement si toutes les verifications ont passe ----------
    audit_corrections(new_fields)
    if doc.kind == "FACTURE" and body.direction and body.direction != old_direction:
        log_action(
            db, doc.company_id, user.id, "PIECE_CORRIGEE", "document", doc.id,
            field="direction", old=old_direction, new=body.direction,
        )

    if body.action == "validate":
        # Version immuable de la piece validee, puis audit avec les champs valides (D2)
        try:
            result = create_or_confirm_snapshot(
                db, doc, new_fields, user.id, extraction_method=method, issues=issues_payload
            )
        except StructureError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        log_action(
            db, doc.company_id, user.id, "PIECE_VALIDEE", "document", doc.id,
            new=audit_payload(result, new_fields),
        )
    else:
        # La piece n'est plus VALIDATED : sa version validee n'est plus valable
        withdraw_current(db, doc, user.id, "REOPENED_SAVE")

    doc.extracted_data = {
        "fields": new_fields,
        "issues": issues_payload,
        "method": method,
    }

    # Une période passe de OPEN à DOCUMENTS_IN_PROGRESS
    # lorsqu'au moins un document de cette période est validé.
    if body.action == "validate" and doc.tax_period_id is not None:
        tax_period = db.get(TaxPeriod, doc.tax_period_id)

        if tax_period is not None and tax_period.status == "OPEN":
            tax_period.status = "DOCUMENTS_IN_PROGRESS"

    db.commit()

    return PieceDetailOut(
        **_to_piece_out(doc),
        error=doc.error,
        extracted_data=doc.extracted_data,
        is_fully_replicated=document_storage.is_fully_replicated(db, doc),
    )