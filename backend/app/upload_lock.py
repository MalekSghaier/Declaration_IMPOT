"""Verrou d'upload pour les pieces a emplacement unique par societe (PATENTE, RNE).

Ces deux types partagent UNE ligne Document par societe : deux ecritures simultanees
s'ecraseraient. Le verrou est NON bloquant : le second televersement recoit une
erreur claire (409) au lieu d'attendre ou de corrompre la ligne.

PostgreSQL : verrou consultatif (advisory lock) tenu sur une connexion dediee, donc
insensible aux commit() faits pendant le traitement. Autres bases (SQLite des tests) :
sans effet.
"""
import contextlib
import logging

from sqlalchemy import text

from app.database import engine

logger = logging.getLogger("app.upload_lock")

_NAMESPACE = {"PATENTE": 1001, "RNE": 1002}


class UploadInProgress(Exception):
    """Un autre televersement du meme type est en cours pour cette societe."""


@contextlib.contextmanager
def single_slot_lock(company_id: int, kind: str):
    if engine.dialect.name != "postgresql":
        yield
        return

    params = {"k1": _NAMESPACE[kind], "k2": company_id}
    conn = engine.connect()
    try:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:k1, :k2)"), params).scalar()
        conn.commit()
        if not acquired:
            raise UploadInProgress(f"{kind} societe {company_id}")
        try:
            yield
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k1, :k2)"), params)
                conn.commit()
            except Exception:
                # Si le deverrouillage echoue, on jette la connexion : sa fermeture libere le verrou.
                logger.exception("deverrouillage impossible, connexion invalidee")
                conn.invalidate()
    finally:
        conn.close()