"""Routes de lecture des versions validees et du contexte FISCAL. Aucune ecriture, aucune decision fiscale."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.fiscal_data import current_snapshots, inventory, snapshot_detail, validated_without_snapshot
from app.intervals import StructureError
from app.models import User
from app.snapshots import list_snapshots
from app.tax_period_service import get_period

router = APIRouter(prefix="/api", tags=["fiscal-data"])


def _http(exc: StructureError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if exc.code.endswith("_NOT_FOUND") else status.HTTP_400_BAD_REQUEST
    return HTTPException(code, str(exc))


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    collection: str
    item_index: int
    fact_code: str
    value_type: str
    value_decimal: Decimal | None = None
    value_date: date | None = None
    value_text: str | None = None
    unit: str | None = None
    reliability: str
    reason_code: str | None = None
    raw_value: Any = None
    source_path: str


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    document_id: int
    version: int
    status: str
    kind: str
    direction: str | None = None
    tax_period_id: int | None = None
    fingerprint: str
    normalizer_version: str
    extraction_method: str | None = None
    origin: str
    validated_by: int | None = None
    validated_at: datetime
    closed_at: datetime | None = None
    closed_by: int | None = None
    closed_reason: str | None = None


class TaxPeriodRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    period_type: str
    period_start: date
    period_end: date
    status: str


class SnapshotDetailOut(SnapshotOut):
    validated_fields: dict
    issues: Any = None
    has_raw: bool
    tax_period: TaxPeriodRef | None = None
    facts: list[FactOut]
    anomalies: list[FactOut]  # faits non fiables ou absents : non exploitables tant que non resolus


class InventoryEntry(BaseModel):
    kind: str
    direction: str | None = None
    snapshots: int
    facts: dict[str, int]


class PeriodFiscalDataOut(BaseModel):
    tax_period: TaxPeriodRef
    inventory: list[InventoryEntry]
    snapshots: list[SnapshotOut]
    validated_without_snapshot: list[int]


@router.get("/pieces/{piece_id}/snapshots", response_model=list[SnapshotOut])
def get_piece_snapshots(piece_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        rows = list_snapshots(db, user.company_id, piece_id)
    except StructureError as exc:
        raise _http(exc)
    return [SnapshotOut.model_validate(r) for r in rows]


@router.get("/pieces/{piece_id}/snapshot", response_model=SnapshotDetailOut)
def get_piece_snapshot(piece_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        detail = snapshot_detail(db, user.company_id, piece_id)
    except StructureError as exc:
        raise _http(exc)
    facts = [FactOut.model_validate(f) for f in detail.facts]
    snap = detail.snapshot
    return SnapshotDetailOut(
        **SnapshotOut.model_validate(snap).model_dump(),
        validated_fields=snap.validated_fields,
        issues=snap.issues,
        has_raw=detail.has_raw,
        tax_period=TaxPeriodRef.model_validate(detail.tax_period) if detail.tax_period else None,
        facts=facts,
        anomalies=[f for f in facts if f.reliability != "RELIABLE"],
    )


@router.get("/tax-periods/{period_id}/fiscal-data", response_model=PeriodFiscalDataOut)
def get_period_fiscal_data(period_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        period = get_period(db, user.company_id, period_id)
    except StructureError as exc:
        raise _http(exc)
    return PeriodFiscalDataOut(
        tax_period=TaxPeriodRef.model_validate(period),
        inventory=[InventoryEntry(**e) for e in inventory(db, user.company_id, period.id)],
        snapshots=[SnapshotOut.model_validate(s) for s in current_snapshots(db, user.company_id, period.id)],
        validated_without_snapshot=validated_without_snapshot(db, user.company_id, period.id),
    )