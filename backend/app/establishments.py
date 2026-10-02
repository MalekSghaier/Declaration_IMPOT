"""Etablissements d'un contribuable : un siege et des secondaires, historises.

Le numero d'etablissement est stocke tel que fourni. AUCUNE regle n'est codee ici : ni format,
ni "000 = siege". Le siege est un indicateur (is_head_office) independant du numero.
Pas de commit ici (a l'appelant).
"""
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import log_action
from app.intervals import StructureError, check_period, overlaps
from app.models import Establishment
from app.profile import STATUSES

NUMBER_STATUSES = ("NEEDS_VERIFICATION", "CONFIRMED")

# Valeur utilisee UNIQUEMENT pour la reprise initiale du siege (migration et premiere confirmation).
# Ce n'est pas une regle fiscale : la convention officielle reste a verifier aupres de la DGI,
# d'ou le statut NEEDS_VERIFICATION systematique.
DEFAULT_HEAD_OFFICE_NUMBER = "000"


def add_establishment(
    db: Session,
    company_id: int,
    address: str,
    user_id: int | None,
    *,
    is_head_office: bool = False,
    number: str | None = None,
    number_status: str = "NEEDS_VERIFICATION",
    valid_from: date | None = None,
    valid_to: date | None = None,
    status: str = "CONFIRMED",
    reason: str | None = None,
) -> Establishment:
    address = (address or "").strip()
    if not address:
        raise StructureError("ESTABLISHMENT_ADDRESS_EMPTY", "l'adresse de l'etablissement est vide")
    number = (number or "").strip() or None
    if number is not None and len(number) > 10:
        raise StructureError("ESTABLISHMENT_NUMBER_INVALID", "numero trop long (10 caracteres max)")
    if number_status not in NUMBER_STATUSES:
        raise StructureError("NUMBER_STATUS_INVALID", f"statut du numero invalide : {number_status!r}")
    if status not in STATUSES:
        raise StructureError("STATUS_INVALID", f"statut invalide : {status!r}")
    check_period(valid_from, valid_to)

    others = db.scalars(select(Establishment).where(Establishment.company_id == company_id))
    for other in others:
        if not overlaps(valid_from, valid_to, other.valid_from, other.valid_to):
            continue
        if is_head_office and other.is_head_office:
            raise StructureError(
                "ESTABLISHMENT_HEAD_OFFICE_OVERLAP", f"un siege existe deja sur cette periode : {other.address!r}"
            )
        if number is not None and other.number == number:
            raise StructureError(
                "ESTABLISHMENT_NUMBER_OVERLAP", f"le numero {number!r} est deja utilise sur cette periode"
            )

    row = Establishment(
        company_id=company_id,
        number=number,
        number_status=number_status,
        is_head_office=is_head_office,
        address=address,
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        reason=reason,
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    log_action(
        db, company_id, user_id, "ETABLISSEMENT_AJOUTE", "establishment", row.id,
        new={"address": address, "number": number, "is_head_office": is_head_office}, reason=reason,
    )
    return row


def end_establishment(
    db: Session,
    company_id: int,
    establishment_id: int,
    end_date: date,
    user_id: int | None,
    reason: str | None = None,
) -> Establishment:
    row = db.scalar(
        select(Establishment).where(Establishment.id == establishment_id, Establishment.company_id == company_id)
    )
    if row is None:
        raise StructureError("ESTABLISHMENT_NOT_FOUND", f"etablissement {establishment_id} introuvable")
    if row.valid_to is not None:
        raise StructureError("ESTABLISHMENT_ALREADY_ENDED", f"etablissement deja cloture le {row.valid_to}")
    check_period(row.valid_from, end_date)
    log_action(
        db, company_id, user_id, "ETABLISSEMENT_CLOTURE", "establishment", row.id,
        field="valid_to", old=None, new=end_date, reason=reason,
    )
    row.valid_to = end_date
    return row


def establishments_as_of(db: Session, company_id: int, on_date: date | None = None) -> list[Establishment]:
    """Etablissements en vigueur a la date donnee (aujourd'hui par defaut), le siege en premier."""
    d = on_date or date.today()
    return list(
        db.scalars(
            select(Establishment)
            .where(
                Establishment.company_id == company_id,
                or_(Establishment.valid_from.is_(None), Establishment.valid_from <= d),
                or_(Establishment.valid_to.is_(None), Establishment.valid_to > d),
            )
            .order_by(Establishment.is_head_office.desc(), Establishment.id)
        )
    )