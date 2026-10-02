"""Journal d'audit : une seule fonction d'ecriture, sans commit.

L'appelant fait le commit : la modification et sa ligne d'audit
sont ecrites dans la meme transaction (tout passe, ou rien).
"""
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.models import AuditLog


def log_action(
    db: Session,
    company_id: int,
    user_id: int | None,
    action: str,
    object_type: str,
    object_id: int | None,
    field: str | None = None,
    old: Any = None,
    new: Any = None,
    reason: str | None = None,
) -> None:
    db.add(
        AuditLog(
            company_id=company_id,
            user_id=user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            field=field,
            old_value=jsonable_encoder(old),
            new_value=jsonable_encoder(new),
            reason=reason,
        )
    )


def diff_fields(old: dict, new: dict) -> list[tuple[str, Any, Any]]:
    """Liste des (champ, ancienne valeur, nouvelle valeur) pour les champs qui different."""
    keys = list(dict.fromkeys([*old, *new]))
    return [(k, old.get(k), new.get(k)) for k in keys if old.get(k) != new.get(k)]