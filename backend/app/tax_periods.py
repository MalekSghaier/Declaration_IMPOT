"""Routes des periodes fiscales. Isolation : toutes les requetes passent par user.company_id."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.intervals import StructureError
from app.models import Company, Document, TaxPeriod, User
from app.tax_period_service import (
    ENABLED_TRANSITIONS, PERIOD_TYPES, STATUSES, change_status, create_period, get_period,
    update_period_dates, ACCEPTING_DOCUMENTS, 
)

router = APIRouter(prefix="/api/tax-periods", tags=["tax-periods"])

_NOT_FOUND = {"TAX_PERIOD_NOT_FOUND"}
_CONFLICT = {
    "TAX_PERIOD_DUPLICATE", "TAX_PERIOD_OVERLAP", "TAX_PERIOD_NOT_EDITABLE", "TAX_PERIOD_LOCKED_BY_DOCUMENTS",
    "TAX_PERIOD_TRANSITION_INVALID", "TAX_PERIOD_TRANSITION_NOT_AVAILABLE", "TAX_PERIOD_NOT_ACCEPTING_DOCUMENTS",
}


def to_http(exc: StructureError) -> HTTPException:
    """404 introuvable, 409 conflit d'etat ou de donnees, 400 pour le reste."""
    if exc.code in _NOT_FOUND:
        code = status.HTTP_404_NOT_FOUND
    elif exc.code in _CONFLICT:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(code, str(exc))


class TaxPeriodIn(BaseModel):
    period_type: str
    period_start: date
    period_end: date


class TaxPeriodPatch(BaseModel):
    """Soit un changement de statut (status + reason), soit une correction des dates, jamais les deux."""
    status: str | None = None
    reason: str | None = None
    period_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None


class TaxPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    period_type: str
    period_start: date
    period_end: date
    status: str
    document_count: int
    editable: bool
    accepts_documents: bool
    available_transitions: list[str]
    created_at: datetime
    updated_at: datetime


def _require_locked(db: Session, user: User) -> None:
    """Meme garde que le depot de pieces (pieces.upload)."""
    company = db.get(Company, user.company_id)
    if company.status != "LOCKED":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Terminez d'abord l'onboarding de la societe")


def _counts(db: Session, company_id: int, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = db.execute(
        select(Document.tax_period_id, func.count())
        .where(Document.company_id == company_id, Document.tax_period_id.in_(ids))
        .group_by(Document.tax_period_id)
    ).all()
    return {period_id: n for period_id, n in rows}


def _to_out(row: TaxPeriod, count: int) -> TaxPeriodOut:
    return TaxPeriodOut(
        id=row.id, period_type=row.period_type, period_start=row.period_start, period_end=row.period_end,
        status=row.status, document_count=count, editable=row.status == "OPEN" and count == 0,
        accepts_documents=row.status in ACCEPTING_DOCUMENTS,
        available_transitions=sorted(b for a, b in ENABLED_TRANSITIONS if a == row.status),
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.post("", response_model=TaxPeriodOut, status_code=status.HTTP_201_CREATED)
def create_tax_period(body: TaxPeriodIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_locked(db, user)
    try:
        row = create_period(db, user.company_id, body.period_type, body.period_start, body.period_end, user.id)
    except StructureError as exc:
        db.rollback()
        raise to_http(exc)
    db.commit()
    return _to_out(row, 0)


@router.get("", response_model=list[TaxPeriodOut])
def list_tax_periods(
    period_type: str | None = None,
    status_: str | None = Query(None, alias="status"),
    year: int | None = Query(None, ge=1, le=9999),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conditions = [TaxPeriod.company_id == user.company_id]
    if period_type is not None:
        if period_type not in PERIOD_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"TAX_PERIOD_TYPE_INVALID : {period_type!r}")
        conditions.append(TaxPeriod.period_type == period_type)
    if status_ is not None:
        if status_ not in STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"TAX_PERIOD_STATUS_INVALID : {status_!r}")
        conditions.append(TaxPeriod.status == status_)
    if year is not None:
        conditions.append(TaxPeriod.period_start.between(date(year, 1, 1), date(year, 12, 31)))
    rows = db.scalars(
        select(TaxPeriod).where(*conditions).order_by(TaxPeriod.period_start.desc(), TaxPeriod.id.desc())
    ).all()
    counts = _counts(db, user.company_id, [r.id for r in rows])
    return [_to_out(r, counts.get(r.id, 0)) for r in rows]


@router.get("/{period_id}", response_model=TaxPeriodOut)
def get_tax_period(period_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        row = get_period(db, user.company_id, period_id)
    except StructureError as exc:
        raise to_http(exc)
    return _to_out(row, _counts(db, user.company_id, [row.id]).get(row.id, 0))


@router.patch("/{period_id}", response_model=TaxPeriodOut)
def patch_tax_period(
    period_id: int, body: TaxPeriodPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _require_locked(db, user)
    temporal = any(v is not None for v in (body.period_type, body.period_start, body.period_end))
    if body.status is not None and temporal:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "TAX_PERIOD_PATCH_INVALID : modifiez soit le statut, soit les dates, pas les deux",
        )
    if body.status is None and not temporal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "TAX_PERIOD_PATCH_INVALID : rien a modifier")
    try:
        if body.status is not None:
            row = change_status(db, user.company_id, period_id, body.status, user.id, body.reason)
        else:
            row = update_period_dates(
                db, user.company_id, period_id, user.id,
                period_type=body.period_type, period_start=body.period_start,
                period_end=body.period_end, reason=body.reason,
            )
    except StructureError as exc:
        db.rollback()
        raise to_http(exc)
    db.commit()
    return _to_out(row, _counts(db, user.company_id, [row.id]).get(row.id, 0))