"""Versions validees des pieces (document_snapshots) et leurs faits normalises (snapshot_facts).

Un snapshot est immuable. Cycle de vie (meme transaction que la piece et l'audit) :
- validation, aucun snapshot courant         -> nouveau CURRENT ;
- validation, meme empreinte que le CURRENT  -> aucune ecriture ;
- validation, empreinte differente           -> l'ancien devient SUPERSEDED, nouveau CURRENT ;
- piece sortie de VALIDATED (save, reject)   -> le CURRENT devient WITHDRAWN.
Invariant : un CURRENT existe si et seulement si la piece est VALIDATED.
Pas de commit ici (a l'appelant). Le backfill utilise exactement les memes fonctions.

La devise est resolue ici (liste de reference CURRENCY, via une Session) puis transmise au normaliseur,
qui reste pur. Devise inconnue ou ambigue : fail closed (voir app.normalization).
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import log_action
from app.intervals import StructureError
from app.models import Document, DocumentSnapshot, SnapshotFact
from app.normalization import (
    CATALOG,
    NORMALIZER_VERSION,
    RELIABLE,
    UNRELIABLE,
    Parsed,
    currency_inputs,
    normalize_document,
)
from app.reference import ReferenceAmbiguous, resolve_reference
from app.services.schemas import InvoiceExtraction, PayslipExtraction

ORIGINS = ("REVIEW", "BACKFILL")
SCHEMAS = {"FACTURE": InvoiceExtraction, "FICHE_PAIE": PayslipExtraction}
CURRENCY_CATEGORY = "CURRENCY"


@dataclass
class SnapshotResult:
    snapshot: DocumentSnapshot
    created: bool  # False : validation identique, version existante conservee
    superseded: DocumentSnapshot | None = None


def compute_fingerprint(kind: str, direction: str | None, tax_period_id: int | None, fields: dict) -> str:
    """SHA-256 du JSON canonique (cles triees, sans espaces)."""
    payload = {"kind": kind, "direction": direction, "tax_period_id": tax_period_id, "fields": fields}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def canonical_fields(kind: str, fields: dict) -> dict:
    """Forme canonique d'une piece (celle d'une validation par la relecture) : passage par le schema Pydantic."""
    schema = SCHEMAS.get(kind)
    if schema is None:
        raise StructureError("SNAPSHOT_KIND_UNSUPPORTED", f"type de piece non supporte : {kind!r}")
    try:
        return schema.model_validate(fields).model_dump(mode="json")
    except Exception as exc:
        raise StructureError("SNAPSHOT_FIELDS_INVALID", "champs valides illisibles") from exc


def get_document(db: Session, company_id: int, document_id: int) -> Document:
    """Piece de la societe, sinon DOCUMENT_NOT_FOUND (jamais d'acces par identifiant seul)."""
    doc = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.company_id == company_id, Document.kind.in_(tuple(CATALOG))
        )
    )
    if doc is None:
        raise StructureError("DOCUMENT_NOT_FOUND", f"piece {document_id} introuvable")
    return doc


def current_snapshot(db: Session, document_id: int, company_id: int) -> DocumentSnapshot | None:
    return db.scalar(
        select(DocumentSnapshot).where(
            DocumentSnapshot.document_id == document_id,
            DocumentSnapshot.company_id == company_id,
            DocumentSnapshot.status == "CURRENT",
        )
    )

def is_stale(snapshot: DocumentSnapshot) -> bool:
    """Vrai si le snapshot a ete produit par une autre version du normaliseur que la version courante.
    Ne modifie rien : l'historique n'est jamais reecrit automatiquement (D3)."""
    return snapshot.normalizer_version != NORMALIZER_VERSION


def stale_snapshots(db: Session, company_id: int, tax_period_id: int | None = None) -> list[DocumentSnapshot]:
    """Snapshots CURRENT d'une societe (et d'une periode si precisee) produits par un autre normaliseur."""
    stmt = select(DocumentSnapshot).where(
        DocumentSnapshot.company_id == company_id,
        DocumentSnapshot.status == "CURRENT",
        DocumentSnapshot.normalizer_version != NORMALIZER_VERSION,
    )
    if tax_period_id is not None:
        stmt = stmt.where(DocumentSnapshot.tax_period_id == tax_period_id)
    return list(db.scalars(stmt.order_by(DocumentSnapshot.document_id)))



def list_snapshots(db: Session, company_id: int, document_id: int) -> list[DocumentSnapshot]:
    doc = get_document(db, company_id, document_id)
    return list(
        db.scalars(
            select(DocumentSnapshot)
            .where(DocumentSnapshot.document_id == doc.id, DocumentSnapshot.company_id == company_id)
            .order_by(DocumentSnapshot.version)
        )
    )


def resolve_currencies(db: Session, kind: str, fields: dict) -> dict[str, Parsed]:
    """Resout les devises d'une piece via la liste de reference CURRENCY : {fact_code: Parsed}.
    Devise absente : aucune entree (rien a resoudre). Inconnue ou ambigue : UNRELIABLE (fail closed).
    Aucun code de devise n'est connu ici : tout vient de reference_values."""
    resolved: dict[str, Parsed] = {}
    for code, raw in currency_inputs(kind, fields).items():
        if not isinstance(raw, str):
            resolved[code] = Parsed(UNRELIABLE, "NOT_TEXT")
            continue
        try:
            ref = resolve_reference(db, CURRENCY_CATEGORY, raw)
        except ReferenceAmbiguous:
            resolved[code] = Parsed(UNRELIABLE, "CURRENCY_AMBIGUOUS")
            continue
        if ref is None:
            resolved[code] = Parsed(UNRELIABLE, "CURRENCY_UNKNOWN")
        else:
            resolved[code] = Parsed(RELIABLE, value_text=ref.code)
    return resolved


def create_or_confirm_snapshot(
    db: Session,
    doc: Document,
    fields: dict,
    user_id: int | None,
    *,
    origin: str = "REVIEW",
    extraction_method: str | None = None,
    issues: list | None = None,
    validated_at: datetime | None = None,
) -> SnapshotResult:
    """Cree la version validee d'une piece (ou confirme l'existante si rien n'a change).

    `fields` : champs valides (forme canonique). direction et tax_period_id sont lus sur la piece.
    SNAPSHOT_CONFLICT : deux validations concurrentes ; l'appelant doit annuler sa transaction.
    """
    if origin not in ORIGINS:
        raise StructureError("SNAPSHOT_ORIGIN_INVALID", f"origine invalide : {origin!r}")
    if doc.kind not in CATALOG:
        raise StructureError("SNAPSHOT_KIND_UNSUPPORTED", f"type de piece non supporte : {doc.kind!r}")

    encoded = jsonable_encoder(fields)
    fingerprint = compute_fingerprint(doc.kind, doc.direction, doc.tax_period_id, encoded)

    current = current_snapshot(db, doc.id, doc.company_id)
    if current is not None and current.fingerprint == fingerprint:
        return SnapshotResult(current, created=False)

    resolved = resolve_currencies(db, doc.kind, encoded)

    now = datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            if current is not None:
                current.status = "SUPERSEDED"
                current.closed_at = now
                current.closed_by = user_id
                current.closed_reason = "REVALIDATED"
                db.flush()  # le CURRENT doit etre ferme avant d'inserer le suivant (index unique)
            version = (
                db.scalar(select(func.max(DocumentSnapshot.version)).where(DocumentSnapshot.document_id == doc.id))
                or 0
            ) + 1
            snapshot = DocumentSnapshot(
                document_id=doc.id,
                company_id=doc.company_id,
                tax_period_id=doc.tax_period_id,
                kind=doc.kind,
                direction=doc.direction,
                version=version,
                status="CURRENT",
                validated_fields=encoded,
                fingerprint=fingerprint,
                normalizer_version=NORMALIZER_VERSION,
                extraction_method=extraction_method,
                issues=jsonable_encoder(issues),
                origin=origin,
                validated_by=user_id,
                validated_at=validated_at or now,
            )
            db.add(snapshot)
            db.flush()
            for fact in normalize_document(doc.kind, encoded, resolved=resolved):
                db.add(
                    SnapshotFact(
                        snapshot_id=snapshot.id,
                        collection=fact.collection,
                        item_index=fact.item_index,
                        fact_code=fact.fact_code,
                        value_type=fact.value_type,
                        value_decimal=fact.value_decimal,
                        value_date=fact.value_date,
                        value_text=fact.value_text,
                        unit=fact.unit,
                        reliability=fact.reliability,
                        reason_code=fact.reason_code,
                        raw_value=jsonable_encoder(fact.raw_value),
                        source_path=fact.source_path,
                    )
                )
            db.flush()
    except IntegrityError as exc:
        raise StructureError(
            "SNAPSHOT_CONFLICT", "la piece a ete validee en meme temps par une autre requete, reessayez"
        ) from exc
    return SnapshotResult(snapshot, created=True, superseded=current)


def withdraw_current(db: Session, doc: Document, user_id: int | None, reason: str) -> DocumentSnapshot | None:
    """Retire le snapshot courant (la piece n'est plus VALIDATED). No-op s'il n'y en a pas. Ecrit l'audit."""
    snapshot = current_snapshot(db, doc.id, doc.company_id)
    if snapshot is None:
        return None
    snapshot.status = "WITHDRAWN"
    snapshot.closed_at = datetime.now(timezone.utc)
    snapshot.closed_by = user_id
    snapshot.closed_reason = reason
    log_action(
        db, doc.company_id, user_id, "SNAPSHOT_RETIRE", "document", doc.id,
        old={"snapshot_id": snapshot.id, "version": snapshot.version, "fingerprint": snapshot.fingerprint},
        reason=reason,
    )
    return snapshot


def audit_payload(result: SnapshotResult, fields: dict) -> dict:
    """Contenu de audit_log.new_value pour PIECE_VALIDEE (D2) : reference du snapshot et champs valides."""
    s = result.snapshot
    return {
        "snapshot_id": s.id,
        "version": s.version,
        "fingerprint": s.fingerprint,
        "kind": s.kind,
        "direction": s.direction,
        "tax_period_id": s.tax_period_id,
        "unchanged": not result.created,
        "fields": fields,
    }