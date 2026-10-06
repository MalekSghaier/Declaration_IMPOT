"""Periodes fiscales d'un contribuable : creation, modification, changement de statut.

Conteneur temporel uniquement : aucune regle metier de calcul ici. Les erreurs sont des StructureError
(code + message), converties en reponses HTTP par app.tax_periods. Pas de commit (a l'appelant).
period_end est INCLUSIVE.
"""
import calendar
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import log_action
from app.intervals import StructureError, overlaps
from app.models import Document, TaxPeriod

PERIOD_TYPES = ("MONTH", "QUARTER", "YEAR")
STATUSES = (
    "OPEN", "DOCUMENTS_IN_PROGRESS", "CALCULATED", "READY_FOR_REVIEW", "VALIDATED", "FINALIZED", "ARCHIVED",
)

# Machine d'etats complete : transitions permises par le metier.
TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"DOCUMENTS_IN_PROGRESS"},
    "DOCUMENTS_IN_PROGRESS": {"CALCULATED"},
    "CALCULATED": {"READY_FOR_REVIEW", "DOCUMENTS_IN_PROGRESS"},
    "READY_FOR_REVIEW": {"VALIDATED", "DOCUMENTS_IN_PROGRESS"},
    "VALIDATED": {"FINALIZED", "READY_FOR_REVIEW"},
    "FINALIZED": {"ARCHIVED"},
    "ARCHIVED": set(),
}
ALL_TRANSITIONS = frozenset((a, b) for a, nexts in TRANSITIONS.items() for b in nexts)
# Retours en arriere : un motif est exige.
BACKWARD_TRANSITIONS = frozenset({
    ("CALCULATED", "DOCUMENTS_IN_PROGRESS"),
    ("READY_FOR_REVIEW", "DOCUMENTS_IN_PROGRESS"),
    ("VALIDATED", "READY_FOR_REVIEW"),
})
# Transitions ouvertes a l'API a ce stade. Les autres seront ajoutees ici, etape par etape.
ENABLED_TRANSITIONS = frozenset({("OPEN", "DOCUMENTS_IN_PROGRESS")})
# Statuts dans lesquels une periode accepte encore des depots de documents.
ACCEPTING_DOCUMENTS = ("OPEN", "DOCUMENTS_IN_PROGRESS")

_MONTH_RE = re.compile(r"[0-9]{4}-[0-9]{2}")  # chiffres ASCII uniquement


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_month(value) -> tuple[date, date] | None:
    """(premier jour, dernier jour) du mois AAAA-MM, ou None si ce n'est pas une vraie date."""
    if not isinstance(value, str) or not _MONTH_RE.fullmatch(value):
        return None
    year, month = int(value[:4]), int(value[5:7])
    try:
        return date(year, month, 1), _last_day(year, month)
    except ValueError:
        return None


def validate_dates(period_type: str, start: date, end: date) -> None:
    if period_type not in PERIOD_TYPES:
        raise StructureError("TAX_PERIOD_TYPE_INVALID", f"type de periode invalide : {period_type!r}")
    if end < start:
        raise StructureError("TAX_PERIOD_INVALID", f"le debut ({start}) est apres la fin ({end})")
    if period_type == "MONTH":
        ok = start.day == 1 and end == _last_day(start.year, start.month)
    elif period_type == "QUARTER":
        ok = start.day == 1 and start.month in (1, 4, 7, 10) and end == _last_day(start.year, start.month + 2)
    else:  # YEAR : pas de calendrier impose, seulement une duree d'au plus 366 jours
        ok = end > start and (end - start).days <= 365
    if not ok:
        raise StructureError("TAX_PERIOD_INVALID", f"dates incoherentes pour une periode {period_type} : {start} -> {end}")


def _exclusive_end(end: date) -> date | None:
    """Fin inclusive -> fin exclusive (pour reutiliser overlaps). None = sans limite (bord du calendrier)."""
    try:
        return end + timedelta(days=1)
    except OverflowError:
        return None


def _check_no_conflict(
    db: Session, company_id: int, period_type: str, start: date, end: date, exclude_id: int | None = None
) -> None:
    others = [
        p
        for p in db.scalars(
            select(TaxPeriod).where(TaxPeriod.company_id == company_id, TaxPeriod.period_type == period_type)
        )
        if p.id != exclude_id
    ]
    for other in others:
        if other.period_start == start:
            raise StructureError("TAX_PERIOD_DUPLICATE", f"une periode commence déjà le {start}")
    for other in others:
        if overlaps(start, _exclusive_end(end), other.period_start, _exclusive_end(other.period_end)):
            raise StructureError(
                "TAX_PERIOD_OVERLAP",
                f"chevauche la periode {other.period_type} {other.period_start} -> {other.period_end}",
            )


def _persist(db: Session, row: TaxPeriod) -> None:
    """Ecrit la ligne dans un savepoint : un conflit d'unicite ne defait pas le travail de l'appelant."""
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise StructureError("TAX_PERIOD_DUPLICATE", "cette periode existe deja pour ce contribuable") from exc


def get_period(db: Session, company_id: int, period_id: int) -> TaxPeriod:
    """Periode DU contribuable donne, sinon TAX_PERIOD_NOT_FOUND (jamais d'acces par identifiant seul)."""
    row = db.scalar(select(TaxPeriod).where(TaxPeriod.id == period_id, TaxPeriod.company_id == company_id))
    if row is None:
        raise StructureError("TAX_PERIOD_NOT_FOUND", f"periode {period_id} introuvable")
    return row


def has_documents(db: Session, period_id: int) -> bool:
    """Vrai si au moins un document est rattache, quel que soit son statut (REJECTED et FAILED inclus)."""
    return db.scalar(select(Document.id).where(Document.tax_period_id == period_id).limit(1)) is not None


def create_period(
    db: Session,
    company_id: int,
    period_type: str,
    period_start: date,
    period_end: date,
    user_id: int | None,
    *,
    reason: str | None = None,
) -> TaxPeriod:
    validate_dates(period_type, period_start, period_end)
    _check_no_conflict(db, company_id, period_type, period_start, period_end)
    row = TaxPeriod(
        company_id=company_id, period_type=period_type, period_start=period_start,
        period_end=period_end, status="OPEN", created_by=user_id,
    )
    _persist(db, row)
    log_action(
        db, company_id, user_id, "PERIODE_CREEE", "tax_period", row.id,
        new={"period_type": period_type, "period_start": period_start, "period_end": period_end},
        reason=reason,
    )
    return row


def _find_month(db: Session, company_id: int, start: date) -> TaxPeriod | None:
    return db.scalar(
        select(TaxPeriod).where(
            TaxPeriod.company_id == company_id,
            TaxPeriod.period_type == "MONTH",
            TaxPeriod.period_start == start,
        )
    )


def get_or_create_month(
    db: Session, company_id: int, period: str, user_id: int | None, *, reason: str | None = None
) -> TaxPeriod:
    """Periode MONTH correspondant a 'AAAA-MM'. Une valeur qui n'est pas une vraie date est refusee."""
    bounds = parse_month(period)
    if bounds is None:
        raise StructureError("TAX_PERIOD_INVALID", f"periode invalide : {period!r} (format AAAA-MM attendu)")
    start, end = bounds
    existing = _find_month(db, company_id, start)
    if existing is not None:
        return existing
    try:
        return create_period(db, company_id, "MONTH", start, end, user_id, reason=reason)
    except StructureError as exc:
        if exc.code != "TAX_PERIOD_DUPLICATE":
            raise
        existing = _find_month(db, company_id, start)  # creee entre-temps par une autre requete
        if existing is None:
            raise
        return existing


def update_period_dates(
    db: Session,
    company_id: int,
    period_id: int,
    user_id: int | None,
    *,
    period_type: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    reason: str | None = None,
) -> TaxPeriod:
    """Modifie type et dates, seulement si la periode est OPEN et sans aucun document rattache."""
    row = get_period(db, company_id, period_id)
    if row.status != "OPEN":
        raise StructureError("TAX_PERIOD_NOT_EDITABLE", f"periode {row.status} : dates non modifiables")
    if has_documents(db, row.id):
        raise StructureError("TAX_PERIOD_LOCKED_BY_DOCUMENTS", "des documents sont rattaches : dates non modifiables")

    new = {
        "period_type": period_type if period_type is not None else row.period_type,
        "period_start": period_start if period_start is not None else row.period_start,
        "period_end": period_end if period_end is not None else row.period_end,
    }
    changes = [(f, getattr(row, f), v) for f, v in new.items() if getattr(row, f) != v]
    if not changes:
        return row

    validate_dates(new["period_type"], new["period_start"], new["period_end"])
    _check_no_conflict(
        db, company_id, new["period_type"], new["period_start"], new["period_end"], exclude_id=row.id
    )
    for field, old, value in changes:
        log_action(
            db, company_id, user_id, "PERIODE_MODIFIEE", "tax_period", row.id,
            field=field, old=old, new=value, reason=reason,
        )
        setattr(row, field, value)
    _persist(db, row)
    return row


def change_status(
    db: Session,
    company_id: int,
    period_id: int,
    new_status: str,
    user_id: int | None,
    reason: str | None = None,
    *,
    enabled: frozenset = ENABLED_TRANSITIONS,
) -> TaxPeriod:
    """Change le statut selon la machine d'etats. `enabled` = transitions ouvertes (API : ENABLED_TRANSITIONS)."""
    row = get_period(db, company_id, period_id)
    if new_status not in STATUSES:
        raise StructureError("TAX_PERIOD_STATUS_INVALID", f"statut inconnu : {new_status!r}")
    pair = (row.status, new_status)
    if new_status not in TRANSITIONS[row.status]:
        raise StructureError("TAX_PERIOD_TRANSITION_INVALID", f"transition interdite : {row.status} -> {new_status}")
    if pair not in enabled:
        raise StructureError(
            "TAX_PERIOD_TRANSITION_NOT_AVAILABLE",
            f"transition {row.status} -> {new_status} pas encore disponible",
        )
    reason = (reason or "").strip() or None
    if pair in BACKWARD_TRANSITIONS and reason is None:
        raise StructureError("TAX_PERIOD_REASON_REQUIRED", "un motif est exige pour un retour en arriere")
    log_action(
        db, company_id, user_id, "PERIODE_STATUT_MODIFIE", "tax_period", row.id,
        field="status", old=row.status, new=new_status, reason=reason,
    )
    row.status = new_status
    return row


def assert_accepts_documents(row: TaxPeriod) -> None:
    if row.status not in ACCEPTING_DOCUMENTS:
        raise StructureError(
            "TAX_PERIOD_NOT_ACCEPTING_DOCUMENTS",
            f"la periode {row.period_start} -> {row.period_end} est {row.status} : depot impossible",
        )