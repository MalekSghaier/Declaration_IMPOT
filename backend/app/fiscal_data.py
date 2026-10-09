"""Lecture FISCAL (contexte) et acces aux faits normalises pour les etapes 6 et 7.

Regle : les modules de calcul ne lisent JAMAIS extracted_data, original_data ni snapshot_facts en direct ;
ils passent par ce module. Il ne renvoie que les faits RELIABLE de snapshots CURRENT et, a part, la liste
des faits exclus (UNRELIABLE ou ABSENT) avec leur raison. require_reliable() bloque explicitement
(FISCAL_DATA_UNRELIABLE) au lieu d'ignorer silencieusement une donnee requise.
Rien n'est persiste ici et aucune decision fiscale n'est prise (ni taux, ni deductibilite, ni montant fiscal).
"""
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.intervals import StructureError
from app.models import Document, DocumentSnapshot, SnapshotFact, TaxPeriod
from app.normalization import CATALOG, NORMALIZER_VERSION
from app.snapshots import current_snapshot, get_document, is_stale


@dataclass(frozen=True)
class FactView:
    snapshot_id: int
    document_id: int
    kind: str
    direction: str | None
    collection: str
    item_index: int
    fact_code: str
    value_type: str
    value_decimal: Decimal | None
    value_date: date | None
    value_text: str | None
    unit: str | None
    reliability: str
    reason_code: str | None
    raw_value: Any
    source_path: str


@dataclass(frozen=True)
class MissingFact:
    """Fait attendu par le catalogue mais sans aucune ligne dans snapshot_facts. Jamais assimile a zero."""
    snapshot_id: int
    document_id: int
    kind: str
    direction: str | None
    collection: str
    fact_code: str
    reason_code: str  # FACT_NOT_PRESENT (en-tete) ou COLLECTION_NOT_PRESENT

@dataclass(frozen=True)
class StaleSnapshot:
    """Snapshot CURRENT produit par une autre version du normaliseur : ses faits sont a re-verifier."""
    snapshot_id: int
    document_id: int
    kind: str
    normalizer_version: str
    expected_version: str


@dataclass
class ReliableFacts:
    facts: list[FactView] = field(default_factory=list)
    excluded: list[FactView] = field(default_factory=list)
    missing: list[MissingFact] = field(default_factory=list)
    stale: list[StaleSnapshot] = field(default_factory=list)


class FiscalDataUnreliable(StructureError):
    """Une donnee requise est non fiable, absente, non presente, ou issue d'un normaliseur perime."""

    def __init__(self, message: str, excluded: list[FactView],
                 missing: list[MissingFact] | None = None, stale: list[StaleSnapshot] | None = None):
        super().__init__("FISCAL_DATA_UNRELIABLE", message)
        self.excluded = excluded
        self.missing = missing or []
        self.stale = stale or []

@dataclass
class SnapshotDetail:
    snapshot: DocumentSnapshot
    facts: list[FactView]
    tax_period: TaxPeriod | None
    has_raw: bool


def _view(snap: DocumentSnapshot, fact: SnapshotFact) -> FactView:
    return FactView(
        snapshot_id=snap.id, document_id=snap.document_id, kind=snap.kind, direction=snap.direction,
        collection=fact.collection, item_index=fact.item_index, fact_code=fact.fact_code,
        value_type=fact.value_type, value_decimal=fact.value_decimal, value_date=fact.value_date,
        value_text=fact.value_text, unit=fact.unit, reliability=fact.reliability,
        reason_code=fact.reason_code, raw_value=fact.raw_value, source_path=fact.source_path,
    )


def _has_typed_value(view: FactView) -> bool:
    if view.value_type in ("AMOUNT", "RATE"):
        return view.value_decimal is not None
    if view.value_type in ("DATE", "MONTH"):
        return view.value_date is not None
    return view.value_text is not None

def expected_facts(kind: str) -> tuple[tuple[str, str], ...]:
    """Faits attendus pour un type de piece, derives du catalogue de normalisation (une seule source)."""
    spec = CATALOG.get(kind)
    if spec is None:
        return ()
    attendus = [("", s.code) for s in spec.header]
    for name, specs in spec.collections.items():
        attendus += [(name, s.code) for s in specs]
    return tuple(attendus)


def _current(db: Session, company_id: int, tax_period_id: int, kind: str | None, direction: str | None):
    stmt = select(DocumentSnapshot).where(
        DocumentSnapshot.company_id == company_id,
        DocumentSnapshot.tax_period_id == tax_period_id,
        DocumentSnapshot.status == "CURRENT",
    )
    if kind is not None:
        stmt = stmt.where(DocumentSnapshot.kind == kind)
    if direction is not None:
        stmt = stmt.where(DocumentSnapshot.direction == direction)
    return list(db.scalars(stmt.order_by(DocumentSnapshot.document_id)))


def _rows(db: Session, company_id: int, tax_period_id: int, kind: str | None, direction: str | None):
    stmt = (
        select(DocumentSnapshot, SnapshotFact)
        .join(SnapshotFact, SnapshotFact.snapshot_id == DocumentSnapshot.id)
        .where(
            DocumentSnapshot.company_id == company_id,
            DocumentSnapshot.tax_period_id == tax_period_id,
            DocumentSnapshot.status == "CURRENT",
        )
        .order_by(DocumentSnapshot.document_id, SnapshotFact.collection, SnapshotFact.item_index, SnapshotFact.id)
    )
    if kind is not None:
        stmt = stmt.where(DocumentSnapshot.kind == kind)
    if direction is not None:
        stmt = stmt.where(DocumentSnapshot.direction == direction)
    return db.execute(stmt).all()


def get_reliable_facts(
    db: Session,
    company_id: int,
    tax_period_id: int,
    *,
    kind: str | None = None,
    direction: str | None = None,
    collection: str | None = None,
    fact_codes: Sequence[str] | None = None,
) -> ReliableFacts:
    """Faits RELIABLE des snapshots CURRENT d'une periode, les faits exclus avec leur raison, et les faits
    attendus par le catalogue mais sans aucune ligne (missing). Ignorer excluded ET missing est une erreur :
    utiliser require_reliable pour bloquer."""
    result = ReliableFacts()
    present: dict[int, set[tuple[str, str]]] = {}
    for snap, fact in _rows(db, company_id, tax_period_id, kind, direction):
        present.setdefault(snap.id, set()).add((fact.collection, fact.fact_code))
        if collection is not None and fact.collection != collection:
            continue
        if fact_codes is not None and fact.fact_code not in fact_codes:
            continue
        view = _view(snap, fact)
        if fact.reliability == "RELIABLE" and _has_typed_value(view):
            result.facts.append(view)
        else:
            result.excluded.append(view)

    for snap in _current(db, company_id, tax_period_id, kind, direction):
        if is_stale(snap):
            result.stale.append(StaleSnapshot(
                snapshot_id=snap.id, document_id=snap.document_id, kind=snap.kind,
                normalizer_version=snap.normalizer_version, expected_version=NORMALIZER_VERSION,
            ))
        have = present.get(snap.id, set())
        for coll, code in expected_facts(snap.kind):
            if collection is not None and coll != collection:
                continue
            if fact_codes is not None and code not in fact_codes:
                continue
            if (coll, code) not in have:
                result.missing.append(MissingFact(
                    snapshot_id=snap.id, document_id=snap.document_id, kind=snap.kind, direction=snap.direction,
                    collection=coll, fact_code=code,
                    reason_code="COLLECTION_NOT_PRESENT" if coll else "FACT_NOT_PRESENT",
                ))
    return result


def require_reliable(
    db: Session,
    company_id: int,
    tax_period_id: int,
    *,
    kind: str,
    required: Sequence[tuple[str, str]],
    direction: str | None = None,
    allow_stale: bool = False,
) -> list[FactView]:
    wanted = set(required)
    inconnus = wanted - set(expected_facts(kind))
    if inconnus:
        raise StructureError(
            "FISCAL_DATA_REQUIRED_UNKNOWN", f"faits requis inconnus du catalogue pour {kind} : {sorted(inconnus)}"
        )
    found = get_reliable_facts(db, company_id, tax_period_id, kind=kind, direction=direction)
    bad = [e for e in found.excluded if (e.collection, e.fact_code) in wanted]
    gaps = [m for m in found.missing if (m.collection, m.fact_code) in wanted]
    stale = [] if allow_stale else found.stale
    if bad or gaps or stale:
        parts = []
        if bad:
            detail = ", ".join(f"doc {e.document_id} {e.source_path} ({e.reason_code or e.reliability})" for e in bad[:10])
            parts.append(f"{len(bad)} donnee(s) requise(s) non fiable(s) ou absente(s) : {detail}")
        if gaps:
            detail = ", ".join(f"doc {m.document_id} {m.collection or '-'}.{m.fact_code} ({m.reason_code})" for m in gaps[:10])
            parts.append(f"{len(gaps)} donnee(s) requise(s) non presente(s) : {detail}")
        if stale:
            detail = ", ".join(
                f"doc {s.document_id} (normaliseur {s.normalizer_version}, attendu {s.expected_version})" for s in stale[:10]
            )
            parts.append(f"{len(stale)} snapshot(s) produit(s) par une ancienne version du normaliseur : {detail}")
        raise FiscalDataUnreliable(" ; ".join(parts), bad, gaps, stale)
    return [f for f in found.facts if (f.collection, f.fact_code) in wanted]


def current_snapshots(db: Session, company_id: int, tax_period_id: int) -> list[DocumentSnapshot]:
    return list(
        db.scalars(
            select(DocumentSnapshot)
            .where(
                DocumentSnapshot.company_id == company_id,
                DocumentSnapshot.tax_period_id == tax_period_id,
                DocumentSnapshot.status == "CURRENT",
            )
            .order_by(DocumentSnapshot.document_id)
        )
    )


def inventory(db: Session, company_id: int, tax_period_id: int) -> list[dict]:
    """Par type de piece et direction : nombre de snapshots CURRENT et de faits par fiabilite. Sans decision."""
    base = (
        DocumentSnapshot.company_id == company_id,
        DocumentSnapshot.tax_period_id == tax_period_id,
        DocumentSnapshot.status == "CURRENT",
    )
    entries: dict[tuple, dict] = {}
    for kind, direction, n in db.execute(
        select(DocumentSnapshot.kind, DocumentSnapshot.direction, func.count())
        .where(*base)
        .group_by(DocumentSnapshot.kind, DocumentSnapshot.direction)
    ):
        entries[(kind, direction)] = {"kind": kind, "direction": direction, "snapshots": n, "facts": {}}
    for kind, direction, reliability, n in db.execute(
        select(DocumentSnapshot.kind, DocumentSnapshot.direction, SnapshotFact.reliability, func.count())
        .join(SnapshotFact, SnapshotFact.snapshot_id == DocumentSnapshot.id)
        .where(*base)
        .group_by(DocumentSnapshot.kind, DocumentSnapshot.direction, SnapshotFact.reliability)
    ):
        entries[(kind, direction)]["facts"][reliability] = n
    return sorted(entries.values(), key=lambda e: (e["kind"], e["direction"] or ""))


def validated_without_snapshot(db: Session, company_id: int, tax_period_id: int) -> list[int]:
    """Pieces VALIDATED de la periode sans snapshot courant (pieces anciennes non reprises) : a signaler."""
    has_current = (
        select(DocumentSnapshot.id)
        .where(DocumentSnapshot.document_id == Document.id, DocumentSnapshot.status == "CURRENT")
        .exists()
    )
    return list(
        db.scalars(
            select(Document.id)
            .where(
                Document.company_id == company_id,
                Document.tax_period_id == tax_period_id,
                Document.kind.in_(tuple(CATALOG)),
                Document.processing_status == "VALIDATED",
                ~has_current,
            )
            .order_by(Document.id)
        )
    )


def snapshot_detail(db: Session, company_id: int, document_id: int) -> SnapshotDetail:
    """Contexte FISCAL d'une piece : version courante, faits, periode, provenance. Aucun montant fiscal."""
    doc = get_document(db, company_id, document_id)
    snap = current_snapshot(db, doc.id, company_id)
    if snap is None:
        raise StructureError("SNAPSHOT_NOT_FOUND", f"aucune version validee courante pour la piece {document_id}")
    rows = db.scalars(
        select(SnapshotFact)
        .where(SnapshotFact.snapshot_id == snap.id)
        .order_by(SnapshotFact.collection, SnapshotFact.item_index, SnapshotFact.id)
    ).all()
    period = None
    if snap.tax_period_id is not None:
        period = db.scalar(
            select(TaxPeriod).where(TaxPeriod.id == snap.tax_period_id, TaxPeriod.company_id == company_id)
        )
    return SnapshotDetail(
        snapshot=snap, facts=[_view(snap, f) for f in rows], tax_period=period, has_raw=doc.original_data is not None
    )