"""Profil d'un contribuable : informations datees et tracables (table profile_values).

Une ligne = une information (attribute_code) pendant une periode [valid_from, valid_to[ :
valid_to est le premier jour ou la valeur n'est plus valable (None = version en cours).
Aucun code d'information n'est code en dur : toute information peut etre ajoutee sans migration.
"""
import re
from datetime import date
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import log_action
from app.models import ProfileValue

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
STATUSES = ("EXTRACTED", "CORRECTED", "CONFIRMED", "UNCERTAIN")

# Champs de la confirmation qui n'entrent pas dans le profil :
# l'activite deviendra une entite a part (plusieurs activites), etape 2c.
NOT_IN_PROFILE = {"activite"}


class ProfileError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code} : {message}")
        self.code = code


def set_profile_value(
    db: Session,
    company_id: int,
    code: str,
    value: Any,
    user_id: int | None,
    *,
    status: str = "CONFIRMED",
    valid_from: date | None = None,
    reason: str | None = None,
    source_document_id: int | None = None,
    page: int | None = None,
    confidence: float | None = None,
    extracted_field_id: int | None = None,
) -> ProfileValue:
    """Enregistre une information et renvoie la ligne en cours. Pas de commit (a l'appelant).

    - premiere valeur du code : une ligne est creee (valid_from tel que fourni, None si inconnu) ;
    - valeur differente : la ligne en cours est cloturee a la date d'effet (aujourd'hui par defaut)
      et une nouvelle ligne est ouverte a la meme date ; l'ancienne valeur reste lisible ;
    - meme valeur : aucune nouvelle version ; seul un changement de statut est enregistre.

    extracted_field_id (etape 2e) : lien vers la ligne extracted_fields dont la valeur
    provient. Reste None si la valeur ne vient d'aucun document extrait (saisie ou
    correction utilisateur).
    """
    if not CODE_RE.match(code):
        raise ProfileError("PROFILE_CODE_INVALID", f"code d'information invalide : {code!r}")
    if status not in STATUSES:
        raise ProfileError("PROFILE_STATUS_INVALID", f"statut invalide : {status!r}")

    encoded = jsonable_encoder(value)
    current = db.scalar(
        select(ProfileValue).where(
            ProfileValue.company_id == company_id,
            ProfileValue.attribute_code == code,
            ProfileValue.valid_to.is_(None),
        )
    )

    new_from = valid_from
    if current is not None:
        if current.value == encoded:
            if current.status != status:
                log_action(
                    db, company_id, user_id, "PROFIL_STATUT_MODIFIE", "company", company_id,
                    field=code, old=current.status, new=status, reason=reason,
                )
                current.status = status
            # Completer le lien de tracabilite s'il manquait : une premiere confirmation
            # sans document ne doit pas empecher un second passage (ex : re-confirmation
            # apres ajout d'un document) de renseigner la source.
            if extracted_field_id is not None and current.extracted_field_id is None:
                current.extracted_field_id = extracted_field_id
            return current

        new_from = valid_from or date.today()
        if current.valid_from is not None and new_from < current.valid_from:
            raise ProfileError(
                "PROFILE_DATE_INVALID",
                f"{code} : la date d'effet {new_from} precede le debut de la version en cours ({current.valid_from})",
            )
        log_action(
            db, company_id, user_id, "PROFIL_MODIFIE", "company", company_id,
            field=code, old=current.value, new=encoded, reason=reason,
        )
        current.valid_to = new_from
        db.flush()  # la version en cours doit etre cloturee avant d'inserer la suivante (index unique)

    row = ProfileValue(
        company_id=company_id,
        attribute_code=code,
        value=encoded,
        valid_from=new_from,
        valid_to=None,
        status=status,
        source_document_id=source_document_id,
        extracted_field_id=extracted_field_id,
        page=page,
        confidence=confidence,
        reason=reason,
        created_by=user_id,
    )
    db.add(row)
    return row


def values_as_of(db: Session, company_id: int, on_date: date | None = None) -> list[ProfileValue]:
    """Informations du profil en vigueur a la date donnee (aujourd'hui par defaut)."""
    d = on_date or date.today()
    return list(
        db.scalars(
            select(ProfileValue)
            .where(
                ProfileValue.company_id == company_id,
                or_(ProfileValue.valid_from.is_(None), ProfileValue.valid_from <= d),
                or_(ProfileValue.valid_to.is_(None), ProfileValue.valid_to > d),
            )
            .order_by(ProfileValue.attribute_code)
        )
    )