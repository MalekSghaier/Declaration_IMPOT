"""Machine a etats du statut d'un DocumentFile (fichier physique d'un document).

Un fichier passe par les etats suivants :
    PENDING      : cree en base, en attente d'ecriture (aucun lease actif)
    REPLICATING  : un worker detient le lease et ecrit les copies (LOCAL + MINIO)
    REPLICATED   : les deux copies sont ecrites et verifiees (checksum OK)
    FAILED       : echec definitif (a reprendre manuellement)

Transitions autorisees :
    PENDING      -> REPLICATING
    REPLICATING  -> REPLICATED  (succes : les deux copies sont verifiees)
    REPLICATING  -> FAILED      (echec definitif : attempts epuise ou erreur fatale)
    FAILED       -> PENDING     (reprise manuelle : nouvelle tentative apres examen)

Transitions interdites (exemples) :
    PENDING      -> REPLICATED  (on ne saute pas REPLICATING)
    REPLICATED   -> *           (terminal)
"""
from typing import Final


STATUSES: Final[tuple[str, ...]] = ("PENDING", "REPLICATING", "REPLICATED", "FAILED")

ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "PENDING": frozenset({"REPLICATING"}),
    "REPLICATING": frozenset({"REPLICATED", "FAILED"}),
    "REPLICATED": frozenset(),
    "FAILED": frozenset({"PENDING"}),
}


class InvalidTransition(ValueError):
    """Transition de statut interdite par la machine a etats."""


def assert_transition(old: str, new: str) -> None:
    """Verifie que la transition (old -> new) est autorisee.

    Leve InvalidTransition si :
    - old ou new n'est pas un statut connu ;
    - la transition n'est pas dans ALLOWED_TRANSITIONS.
    """
    if old not in STATUSES:
        raise InvalidTransition(f"statut inconnu : {old!r}")
    if new not in STATUSES:
        raise InvalidTransition(f"statut inconnu : {new!r}")
    if new not in ALLOWED_TRANSITIONS[old]:
        raise InvalidTransition(f"transition interdite : {old!r} -> {new!r}")