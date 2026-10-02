"""Lecture des listes de reference (table reference_values).

Aucune valeur n'est codee ici : tout vient de la base. Un texte (code, libelle ou alias)
est rapproche d'une valeur de reference sans tenir compte de la casse, des accents ni des espaces.
"""
import re
import unicodedata
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import ReferenceValue

CATEGORY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")


class ReferenceAmbiguous(Exception):
    """Le texte correspond a plusieurs valeurs de reference differentes."""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def list_valid(db: Session, category: str, on_date: date | None = None) -> list[ReferenceValue]:
    """Valeurs de la categorie valables a la date donnee (aujourd'hui par defaut)."""
    d = on_date or date.today()
    return list(
        db.scalars(
            select(ReferenceValue)
            .where(
                ReferenceValue.category == category,
                or_(ReferenceValue.valid_from.is_(None), ReferenceValue.valid_from <= d),
                or_(ReferenceValue.valid_to.is_(None), ReferenceValue.valid_to > d),
            )
            .order_by(ReferenceValue.code)
        )
    )


def resolve_reference(
    db: Session, category: str, text: str | None, on_date: date | None = None
) -> ReferenceValue | None:
    """Valeur de reference correspondant au texte (code, libelle ou alias), ou None si aucune."""
    if not text or not text.strip():
        return None
    wanted = normalize(text)
    matches = []
    for ref in list_valid(db, category, on_date):
        names = {normalize(ref.code), normalize(ref.label)}
        names.update(normalize(a) for a in (ref.aliases or []))
        if wanted in names:
            matches.append(ref)
    if len(matches) > 1:
        codes = ", ".join(m.code for m in matches)
        raise ReferenceAmbiguous(f"'{text}' correspond a plusieurs valeurs de {category} : {codes}")
    return matches[0] if matches else None