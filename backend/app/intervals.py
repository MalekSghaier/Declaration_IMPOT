"""Periodes [valid_from, valid_to[ : NULL au debut = debut inconnu, NULL a la fin = en cours."""
from datetime import date


class StructureError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code} : {message}")
        self.code = code


def check_period(valid_from: date | None, valid_to: date | None) -> None:
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise StructureError("PERIOD_INVALID", f"la fin ({valid_to}) doit suivre le debut ({valid_from})")


def overlaps(from_a: date | None, to_a: date | None, from_b: date | None, to_b: date | None) -> bool:
    """Vrai si les deux periodes ont au moins un jour en commun."""
    return (to_b is None or from_a is None or from_a < to_b) and (
        to_a is None or from_b is None or from_b < to_a
    )