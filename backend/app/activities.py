"""Activites d'un contribuable : plusieurs, une seule principale par periode, historisees.

Pour changer d'activite principale : end_activity() sur l'ancienne, puis add_activity() sur la nouvelle.
Rien n'est supprime. Pas de commit ici (a l'appelant). Le code de nomenclature n'est jamais renseigne
automatiquement.
"""
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import log_action
from app.intervals import StructureError, check_period, overlaps
from app.models import Activity
from app.profile import STATUSES


def add_activity(
    db: Session,
    company_id: int,
    label: str,
    user_id: int | None,
    *,
    is_primary: bool = False,
    code: str | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    status: str = "CONFIRMED",
    reason: str | None = None,
    source_document_id: int | None = None,
) -> Activity:
    label = (label or "").strip()
    if not label:
        raise StructureError("ACTIVITY_LABEL_EMPTY", "le libelle de l'activite est vide")
    if status not in STATUSES:
        raise StructureError("STATUS_INVALID", f"statut invalide : {status!r}")
    check_period(valid_from, valid_to)

    if is_primary:
        others = db.scalars(
            select(Activity).where(Activity.company_id == company_id, Activity.is_primary.is_(True))
        )
        for other in others:
            if overlaps(valid_from, valid_to, other.valid_from, other.valid_to):
                raise StructureError(
                    "ACTIVITY_PRIMARY_OVERLAP",
                    f"une activite principale existe deja sur cette periode : {other.label!r}",
                )

    row = Activity(
        company_id=company_id,
        label=label,
        code=(code or "").strip() or None,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        source_document_id=source_document_id,
        reason=reason,
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    log_action(
        db, company_id, user_id, "ACTIVITE_AJOUTEE", "activity", row.id,
        new={"label": label, "is_primary": is_primary, "code": row.code}, reason=reason,
    )
    return row


def end_activity(
    db: Session,
    company_id: int,
    activity_id: int,
    end_date: date,
    user_id: int | None,
    reason: str | None = None,
) -> Activity:
    """Cloture une activite a la date donnee (premier jour ou elle n'est plus valable)."""
    row = db.scalar(select(Activity).where(Activity.id == activity_id, Activity.company_id == company_id))
    if row is None:
        raise StructureError("ACTIVITY_NOT_FOUND", f"activite {activity_id} introuvable")
    if row.valid_to is not None:
        raise StructureError("ACTIVITY_ALREADY_ENDED", f"activite deja cloturee le {row.valid_to}")
    check_period(row.valid_from, end_date)
    log_action(
        db, company_id, user_id, "ACTIVITE_CLOTUREE", "activity", row.id,
        field="valid_to", old=None, new=end_date, reason=reason,
    )
    row.valid_to = end_date
    return row


def activities_as_of(db: Session, company_id: int, on_date: date | None = None) -> list[Activity]:
    """Activites en vigueur a la date donnee (aujourd'hui par defaut), la principale en premier."""
    d = on_date or date.today()
    return list(
        db.scalars(
            select(Activity)
            .where(
                Activity.company_id == company_id,
                or_(Activity.valid_from.is_(None), Activity.valid_from <= d),
                or_(Activity.valid_to.is_(None), Activity.valid_to > d),
            )
            .order_by(Activity.is_primary.desc(), Activity.id)
        )
    )