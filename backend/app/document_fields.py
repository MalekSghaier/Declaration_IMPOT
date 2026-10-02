"""Enregistrement et correction des champs extraits d'un document (table extracted_fields).

Une ligne = un champ extrait d'un document donne.
- La valeur courante et le statut sont mutables tant que le champ n'est pas CORRECTED
  ou CONFIRMED.
- Une extraction automatique (nouvelle OCR d'un document) NE DOIT PAS ecraser une
  correction ou une confirmation deja en place.
- Chaque correction ecrit une ligne dans audit_log ; l'ancienne valeur n'est jamais perdue.

Note V1
-------
Les sous-champs des lignes de TVA (numero, taux, base_ht, montant_tva d'une meme ligne)
ne sont PAS des ExtractedField independants. `lignes_tva` est stockee comme un seul champ
JSON, avec sa liste de sous-elements. A revoir dans une version ulterieure si le besoin
de corriger ligne-par-ligne se confirme.

ATTENTION
---------
Ce module importe ExtractedField depuis app.models. Cette classe est ajoutee a l'etape 2b.
Tant que 2b n'est pas appliquee, l'import de ce module echoue. Il ne doit donc etre importe
nulle part avant 2b + 2d (branchement onboarding/tasks).
"""
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_action
from app.intervals import StructureError
from app.models import Document, ExtractedField

# Statuts qui protegent la valeur contre une reecriture par une extraction automatique
PROTECTED_STATUSES = ("CORRECTED", "CONFIRMED")

# Statuts valides pour un ExtractedField (memes valeurs que profile.STATUSES)
STATUSES = ("EXTRACTED", "CORRECTED", "CONFIRMED", "UNCERTAIN")


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _validate_confidence(confidence: float | None) -> None:
    """Refuse une confiance hors de [0, 1]. None est accepte (donnee non renseignee)."""
    if confidence is None:
        return
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        raise StructureError(
            "CONFIDENCE_INVALID",
            f"confidence doit etre un nombre, recu {confidence!r}",
        )
    if not (0.0 <= value <= 1.0):
        raise StructureError(
            "CONFIDENCE_OUT_OF_RANGE",
            f"confidence doit etre entre 0 et 1, recu {value}",
        )


def _get_document(db: Session, document_id: int, company_id: int) -> Document:
    """Verifie que le document existe ET appartient a la societe. Sinon, StructureError."""
    doc = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
    )
    if doc is None:
        raise StructureError(
            "DOCUMENT_NOT_FOUND",
            f"document {document_id} introuvable pour cette societe",
        )
    return doc


def _get_field(db: Session, document_id: int, field_code: str) -> ExtractedField:
    row = db.scalar(
        select(ExtractedField).where(
            ExtractedField.document_id == document_id,
            ExtractedField.field_code == field_code,
        )
    )
    if row is None:
        raise StructureError(
            "EXTRACTED_FIELD_NOT_FOUND",
            f"aucun champ {field_code!r} pour le document {document_id}",
        )
    return row


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def record_extraction(
    db: Session,
    document: Document,
    extraction: BaseModel | dict,
    *,
    method: str | None = None,
) -> list[ExtractedField]:
    """Enregistre les champs extraits d'un document. Pas de commit (a l'appelant).

    Comportement par champ :
      - pas de ligne existante          -> creation, statut EXTRACTED
      - ligne existante EXTRACTED       -> mise a jour de value (nouvelle extraction)
      - ligne existante UNCERTAIN       -> mise a jour, statut redevient EXTRACTED
      - ligne existante CORRECTED       -> NON TOUCHEE
      - ligne existante CONFIRMED       -> NON TOUCHEE

    `extraction` est soit un modele Pydantic (avec model_dump), soit un dict deja construit.
    `method` est la methode de lecture ("native", "ocr", "mock").
    """
    if isinstance(extraction, BaseModel):
        fields: dict[str, Any] = extraction.model_dump(mode="json")
    else:
        fields = dict(extraction)

    existing = {
        row.field_code: row
        for row in db.scalars(
            select(ExtractedField).where(ExtractedField.document_id == document.id)
        )
    }

    result: list[ExtractedField] = []
    for field_code, value in fields.items():
        encoded = jsonable_encoder(value)
        current = existing.get(field_code)

        if current is not None:
            if current.status in PROTECTED_STATUSES:
                # Une correction ou une confirmation ne doit pas etre annulee
                # par une relecture automatique du document.
                result.append(current)
                continue
            current.value = encoded
            current.status = "EXTRACTED"
            if method is not None:
                current.extraction_method = method
            result.append(current)
            continue

        row = ExtractedField(
            document_id=document.id,
            field_code=field_code,
            value=encoded,
            status="EXTRACTED",
            extraction_method=method,
        )
        db.add(row)
        result.append(row)

    db.flush()
    return result


def get_fields(
    db: Session,
    document_id: int,
    company_id: int,
) -> list[ExtractedField]:
    """Champs extraits d'un document, verifie l'appartenance a company_id.

    Leve StructureError("DOCUMENT_NOT_FOUND") si le document n'appartient pas
    a la societe demandee.
    """
    _get_document(db, document_id, company_id)
    return list(
        db.scalars(
            select(ExtractedField)
            .where(ExtractedField.document_id == document_id)
            .order_by(ExtractedField.field_code)
        )
    )


def mark_corrected(
    db: Session,
    document_id: int,
    company_id: int,
    field_code: str,
    new_value: Any,
    user_id: int | None,
    *,
    reason: str | None = None,
) -> ExtractedField:
    """Corrige un champ extrait. Ecrit une ligne AuditLog. Pas de commit.

    Le statut passe a CORRECTED. L'ancienne valeur est conservee dans AuditLog.
    Ne fait rien si la nouvelle valeur est identique a la valeur courante.

    Leve StructureError si le document n'appartient pas a company_id, ou si le
    champ est introuvable.
    """
    _get_document(db, document_id, company_id)

    row = _get_field(db, document_id, field_code)
    encoded_new = jsonable_encoder(new_value)

    if row.value == encoded_new:
        return row

    log_action(
        db,
        company_id,
        user_id,
        "CHAMP_PIECE_CORRIGE",
        "document",
        document_id,
        field=field_code,
        old=row.value,
        new=encoded_new,
        reason=reason,
    )
    row.value = encoded_new
    row.status = "CORRECTED"
    return row


def set_field_trace(
    db: Session,
    document_id: int,
    company_id: int,
    field_code: str,
    *,
    page: int | None = None,
    confidence: float | None = None,
) -> ExtractedField:
    """Renseigne la page et/ou la confidence d'un champ deja enregistre. Pas de commit.

    Refuse une confidence hors de [0, 1].
    """
    _validate_confidence(confidence)
    _get_document(db, document_id, company_id)

    row = _get_field(db, document_id, field_code)
    if page is not None:
        if page < 0:
            raise StructureError("PAGE_INVALID", f"page doit etre >= 0, recu {page}")
        row.page = page
    if confidence is not None:
        row.confidence = confidence
    return row