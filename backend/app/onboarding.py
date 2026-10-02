import hashlib
import json
import logging
import mimetypes
import os
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.activities import add_activity
from app.audit import diff_fields, log_action
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.document_fields import record_extraction
from app.establishments import DEFAULT_HEAD_OFFICE_NUMBER, add_establishment
from app.models import Company, Document, ExtractedField, User
from app.profile import NOT_IN_PROFILE, set_profile_value
from app.reference import ReferenceAmbiguous, resolve_reference
from app.services.document_reader import read_document
from app.services.extraction import extract_patente, extract_rne
from app.services.schemas import CompanyProfile, PatenteExtraction, RNEExtraction

logger = logging.getLogger("app.onboarding")

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


# Donnees fictives utilisees uniquement quand OCR_MOCK=true
MOCK_PATENTE = PatenteExtraction(
    raison_sociale="SOCIETE EXEMPLE SARL",
    matricule_fiscal="1234567/A/M/000",
    adresse="12 Rue de la Liberte, 1002 Tunis",
    activite="Commerce de materiel informatique",
)
MOCK_RNE = RNEExtraction(
    raison_sociale="SOCIETE EXEMPLE",
    matricule_fiscal="1234567/A/M/000",
    forme_juridique="SARL",
    capital=10000.0,
    date_creation=date(2018, 5, 14),
    dirigeant="Ahmed Ben Salah",
    adresse="12 Rue de la Liberte, 1002 Tunis",
)


def _ensure_not_locked(company: Company) -> None:
    if company.status == "LOCKED":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Societe deja verrouillee")


def _service_error(kind: str, step: str, exc: Exception) -> HTTPException:
    """Log le detail technique complet, renvoie un message lisible a l'utilisateur."""
    logger.exception("[%s] echec de %s", kind, step)
    if "429" in str(exc):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{kind} : limite de requetes du service OCR atteinte, reessayez dans quelques minutes.",
        )
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        f"{kind} : echec de {step}. Reessayez, ou contactez le support si le probleme persiste.",
    )


def _extract_fields(raw: bytes, ext: str, kind: str) -> tuple[BaseModel, str]:
    """Renvoie (champs extraits, methode de lecture : 'mock' | 'ocr')."""
    if settings.OCR_MOCK:
        logger.warning("[MOCK] extraction simulee pour %s", kind)
        extracted = MOCK_PATENTE if kind == "PATENTE" else MOCK_RNE
        return extracted, "mock"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        pages = read_document(tmp_path, force_ocr=True)
    except Exception as exc:
        raise _service_error(kind, "la lecture du document", exc) from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not any(p.text.strip() for p in pages):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{kind} : aucun texte lisible dans le document. Verifiez le fichier.",
        )

    try:
        extracted = extract_patente(pages) if kind == "PATENTE" else extract_rne(pages)
    except Exception as exc:
        raise _service_error(kind, "l'extraction des champs", exc) from exc

    return extracted, "ocr"


def _process(company_id: int, kind: str, upload: UploadFile, db: Session) -> BaseModel:
    """Stocke le fichier dans MinIO, l'OCRise si besoin, extrait les champs.

    Trace chaque champ extrait dans extracted_fields (etape 2d). Une nouvelle
    extraction d'un meme document ne peut PAS ecraser un champ CORRECTED ou
    CONFIRMED (protection portee par document_fields.record_extraction).
    """
    raw = upload.file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{kind} : fichier vide")
    if len(raw) > settings.UPLOAD_MAX_FILE_BYTES:
        max_mo = settings.UPLOAD_MAX_FILE_BYTES // (1024 * 1024)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{kind} : fichier trop volumineux ({max_mo} Mo max)")

    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{kind} : format non supporte (PDF, PNG ou JPG)")

    sha256 = hashlib.sha256(raw).hexdigest()
    key = f"{company_id}/{kind.lower()}{ext}"
    storage.upload_bytes(key, raw, upload.content_type or "application/octet-stream")

    extracted, method = _extract_fields(raw, ext, kind)

    doc = db.scalar(select(Document).where(Document.company_id == company_id, Document.kind == kind))
    if doc is None:
        doc = Document(company_id=company_id, kind=kind)
        db.add(doc)
    doc.minio_key = key
    doc.filename = upload.filename or f"{kind.lower()}{ext}"
    doc.sha256 = sha256
    doc.extracted_data = extracted.model_dump(mode="json")
    doc.processing_status = "DONE"

    # flush pour garantir doc.id avant la trace par champ
    db.flush()

    # Trace chaque champ extrait dans extracted_fields.
    record_extraction(db, doc, extracted, method=method)

    return extracted


def _merge(patente: PatenteExtraction, rne: RNEExtraction) -> CompanyProfile:
    # Le LLM renvoie le capital en float (Decimal refuse par Mistral) : on reconvertit ici.
    capital = Decimal(str(rne.capital)) if rne.capital is not None else None
    return CompanyProfile(
        raison_sociale=rne.raison_sociale or patente.raison_sociale,
        matricule_fiscal=patente.matricule_fiscal or rne.matricule_fiscal,
        adresse=patente.adresse or rne.adresse,
        forme_juridique=rne.forme_juridique,
        capital=capital,
        date_creation=rne.date_creation,
        activite=patente.activite,
        dirigeant=rne.dirigeant,
        code_tva=patente.code_tva,
        code_categorie=patente.code_categorie,
    )


def _encode_for_match(value) -> str:
    """Serialisation stable pour comparer un ExtractedField.value a une valeur confirmee.

    jsonable_encoder convertit dates, Decimal, etc. en types JSON-compatibles ; on
    serialise ensuite en JSON avec tri des cles pour une comparaison deterministe.
    """
    return json.dumps(jsonable_encoder(value), sort_keys=True, default=str)


def _build_extracted_index(db: Session, docs: dict[str, Document]) -> dict[str, dict[str, int]]:
    """Index {field_code: {encoded_value: extracted_field_id}} pour les documents du contribuable.

    Permet de retrouver, pour chaque valeur confirmee, la ligne extracted_fields dont
    elle provient. Plusieurs champs de meme nom (ex : matricule_fiscal dans PATENTE et
    RNE) sont indexes par valeur : on retrouve celui dont la valeur correspond.
    """
    if not docs:
        return {}
    doc_ids = [d.id for d in docs.values()]
    index: dict[str, dict[str, int]] = {}
    for ef in db.scalars(select(ExtractedField).where(ExtractedField.document_id.in_(doc_ids))):
        key = _encode_for_match(ef.value)
        index.setdefault(ef.field_code, {})[key] = ef.id
    return index


@router.post("/upload", response_model=CompanyProfile)
def upload(
    patente: UploadFile = File(...),
    rne: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company = db.get(Company, user.company_id)
    _ensure_not_locked(company)

    patente_data = _process(user.company_id, "PATENTE", patente, db)
    rne_data = _process(user.company_id, "RNE", rne, db)
    db.commit()

    return _merge(patente_data, rne_data)


class ConfirmIn(BaseModel):
    raison_sociale: str
    matricule_fiscal: str
    adresse: str | None = None
    forme_juridique: str | None = None
    capital: Decimal | None = None
    date_creation: date | None = None
    activite: str | None = None
    dirigeant: str | None = None
    code_tva: str | None = None
    code_categorie: str | None = None
    # Code ou alias d'une valeur de reference TAXPAYER_TYPE (facultatif)
    taxpayer_type: str | None = None
    # Code ou alias d'une valeur de reference TAX_REGIME (facultatif)
    tax_regime: str | None = None


def _resolve_required_ref(db: Session, category: str, raw: str | None, label: str) -> str | None:
    """Code de reference, ou None si non fourni. Refuse une valeur fournie mais inconnue."""
    if raw is None or not raw.strip():
        return None
    try:
        ref = resolve_reference(db, category, raw)
    except ReferenceAmbiguous as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"REFERENCE_VALUE_AMBIGUOUS : {exc}")
    if ref is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"REFERENCE_VALUE_UNKNOWN : {label} inconnu ({raw}). "
            f"Il doit exister dans les valeurs de reference {category}.",
        )
    return ref.code


@router.post("/confirm")
def confirm(data: ConfirmIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    company = db.get(Company, user.company_id)
    _ensure_not_locked(company)

    confirmed = data.model_dump()
    taxpayer_type = _resolve_required_ref(db, "TAXPAYER_TYPE", confirmed.pop("taxpayer_type"), "type de contribuable")
    tax_regime = _resolve_required_ref(db, "TAX_REGIME", confirmed.pop("tax_regime"), "regime fiscal")

    # Ce que l'OCR avait propose, reconstruit depuis les documents stockes
    docs = {
        d.kind: d
        for d in db.scalars(
            select(Document).where(
                Document.company_id == user.company_id,
                Document.kind.in_(("PATENTE", "RNE")),
            )
        )
    }
    proposed: dict = {}
    if "PATENTE" in docs and "RNE" in docs:
        try:
            proposed = _merge(
                PatenteExtraction.model_validate(docs["PATENTE"].extracted_data),
                RNEExtraction.model_validate(docs["RNE"].extracted_data),
            ).model_dump()
        except Exception:
            logger.exception("proposition OCR illisible pour la societe %s", user.company_id)

    if proposed:
        for field, old, new in diff_fields(proposed, confirmed):
            log_action(
                db, company.id, user.id, "SOCIETE_CORRIGEE", "company", company.id,
                field=field, old=old, new=new,
            )
    log_action(db, company.id, user.id, "SOCIETE_CONFIRMEE", "company", company.id)

    # Index des champs extraits (etape 2e) : permet de tracer chaque valeur confirmee
    # jusqu'a la ligne ExtractedField dont elle provient (None si saisie/correction).
    extracted_index = _build_extracted_index(db, docs)

    # Colonnes de la societe (conservees pour compatibilite avec le code existant)
    company_columns = Company.__table__.columns.keys()
    for field, value in confirmed.items():
        if field in company_columns:
            setattr(company, field, value)
    company.taxpayer_type = taxpayer_type
    company.status = "LOCKED"
    company.locked_at = datetime.now(timezone.utc)

    # Profil date et trace : une ligne par information confirmee (aucune liste de champs ici)
    for code, value in confirmed.items():
        if value is None or code in NOT_IN_PROFILE:
            continue
        set_profile_value(
            db, company.id, code, value, user.id,
            status="CONFIRMED", reason="CONFIRMATION_INITIALE",
            extracted_field_id=extracted_index.get(code, {}).get(_encode_for_match(value)),
        )
    # Regime fiscal : reference dynamique, datee (aucun ExtractedField : liste de reference)
    if tax_regime:
        set_profile_value(
            db, company.id, "tax_regime", tax_regime, user.id,
            status="CONFIRMED", reason="CONFIRMATION_INITIALE",
        )
    # Forme juridique : lien vers la reference LEGAL_FORM seulement si elle existe (liste vide = aucun lien)
    try:
        legal_form = resolve_reference(db, "LEGAL_FORM", confirmed.get("forme_juridique"))
    except ReferenceAmbiguous:
        legal_form = None
    if legal_form is not None:
        set_profile_value(
            db, company.id, "legal_form_code", legal_form.code, user.id,
            status="CONFIRMED", reason="CONFIRMATION_INITIALE",
        )

    # Activite principale et siege : les valeurs confirmees alimentent les nouvelles tables.
    # Le code d'activite reste NULL ; le numero du siege est une valeur de reprise a verifier.
    if confirmed.get("activite"):
        add_activity(
            db, company.id, confirmed["activite"], user.id,
            is_primary=True, status="CONFIRMED", reason="CONFIRMATION_INITIALE",
        )
    if confirmed.get("adresse"):
        add_establishment(
            db, company.id, confirmed["adresse"], user.id,
            is_head_office=True, number=DEFAULT_HEAD_OFFICE_NUMBER, number_status="NEEDS_VERIFICATION",
            status="CONFIRMED", reason="CONFIRMATION_INITIALE",
        )
    db.commit()
    return {"status": "LOCKED"}


@router.get("/document/{kind}/file")
def get_file(kind: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    kind = kind.upper()
    if kind not in ("PATENTE", "RNE"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Type inconnu")
    doc = db.scalar(
        select(Document).where(Document.company_id == user.company_id, Document.kind == kind)
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document non trouve")
    media_type = mimetypes.guess_type(doc.filename)[0] or "application/octet-stream"
    return Response(content=storage.get_bytes(doc.minio_key), media_type=media_type)