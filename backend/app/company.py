from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activities import activities_as_of
from app.database import get_db
from app.deps import get_current_user
from app.establishments import establishments_as_of
from app.models import Activity, Company, Establishment, ProfileValue, User
from app.profile import values_as_of
from app.services.schemas import CompanyProfile

router = APIRouter(prefix="/api/company", tags=["company"])


class CompanyInfoOut(CompanyProfile):
    model_config = ConfigDict(from_attributes=True)
    locked_at: datetime | None = None
    taxpayer_type: str | None = None


class ProfileValueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    attribute_code: str
    value: Any
    valid_from: date | None = None
    valid_to: date | None = None
    status: str
    source_document_id: int | None = None
    page: int | None = None
    confidence: Decimal | None = None
    reason: str | None = None
    created_at: datetime


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    label: str
    code: str | None = None
    is_primary: bool
    valid_from: date | None = None
    valid_to: date | None = None
    status: str
    reason: str | None = None
    created_at: datetime


class EstablishmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str | None = None
    number_status: str
    is_head_office: bool
    address: str
    valid_from: date | None = None
    valid_to: date | None = None
    status: str
    reason: str | None = None
    created_at: datetime


def _locked_company(db: Session, user: User) -> Company:
    company = db.get(Company, user.company_id)
    if company.status != "LOCKED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Onboarding non termine")
    return company


@router.get("/info", response_model=CompanyInfoOut)
def get_info(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    company = _locked_company(db, user)
    return CompanyInfoOut.model_validate(company)


@router.get("/profile", response_model=list[ProfileValueOut])
def get_profile(
    history: bool = False,
    as_of: date | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Profil de la societe. Par defaut : valeurs en vigueur aujourd'hui (ou a la date as_of).
    history=true : toutes les versions de toutes les informations."""
    _locked_company(db, user)
    if history:
        rows = db.scalars(
            select(ProfileValue)
            .where(ProfileValue.company_id == user.company_id)
            .order_by(ProfileValue.attribute_code, ProfileValue.id)
        ).all()
    else:
        rows = values_as_of(db, user.company_id, as_of)
    return [ProfileValueOut.model_validate(r) for r in rows]


@router.get("/activities", response_model=list[ActivityOut])
def get_activities(
    history: bool = False,
    as_of: date | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Activites en vigueur (aujourd'hui ou a la date as_of) ; history=true : toutes les periodes."""
    _locked_company(db, user)
    if history:
        rows = db.scalars(
            select(Activity).where(Activity.company_id == user.company_id).order_by(Activity.id)
        ).all()
    else:
        rows = activities_as_of(db, user.company_id, as_of)
    return [ActivityOut.model_validate(r) for r in rows]


@router.get("/establishments", response_model=list[EstablishmentOut])
def get_establishments(
    history: bool = False,
    as_of: date | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Etablissements en vigueur (aujourd'hui ou a la date as_of) ; history=true : toutes les periodes."""
    _locked_company(db, user)
    if history:
        rows = db.scalars(
            select(Establishment).where(Establishment.company_id == user.company_id).order_by(Establishment.id)
        ).all()
    else:
        rows = establishments_as_of(db, user.company_id, as_of)
    return [EstablishmentOut.model_validate(r) for r in rows]