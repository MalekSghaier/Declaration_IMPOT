"""Controle en LECTURE SEULE de la coherence pieces / snapshots.

Depuis backend\\ : python check_snapshots.py
Verifie : (1) toute piece VALIDATED a un snapshot CURRENT ; (2) tout CURRENT correspond a une piece
VALIDATED ; (3) l'empreinte du CURRENT correspond a la piece ; (4) la periode du snapshot est celle de la piece.
"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Document, DocumentSnapshot
from app.normalization import CATALOG
from app.snapshots import canonical_fields, compute_fingerprint
from app.intervals import StructureError


def main() -> None:
    problems = 0
    with SessionLocal() as db:
        docs = db.scalars(select(Document).where(Document.kind.in_(tuple(CATALOG))).order_by(Document.id)).all()
        current = {s.document_id: s for s in db.scalars(select(DocumentSnapshot).where(DocumentSnapshot.status == "CURRENT"))}
        for doc in docs:
            snap = current.pop(doc.id, None)
            validated = doc.processing_status == "VALIDATED"
            if validated and snap is None:
                print(f"SANS SNAPSHOT  doc={doc.id} (VALIDATED sans version courante : lancer le backfill)")
                problems += 1
            elif snap is not None and not validated:
                print(f"CURRENT A TORT doc={doc.id} statut={doc.processing_status}")
                problems += 1
            elif snap is not None:
                try:
                    fields = canonical_fields(doc.kind, (doc.extracted_data or {}).get("fields") or {})
                except StructureError:
                    print(f"CHAMPS ILLISIBLES doc={doc.id}")
                    problems += 1
                    continue
                if snap.fingerprint != compute_fingerprint(doc.kind, doc.direction, doc.tax_period_id, fields):
                    print(f"DIVERGENT      doc={doc.id} (empreinte differente)")
                    problems += 1
                if snap.tax_period_id != doc.tax_period_id:
                    print(f"PERIODE        doc={doc.id} snapshot={snap.tax_period_id} piece={doc.tax_period_id}")
                    problems += 1
        for document_id in current:
            print(f"ORPHELIN       snapshot CURRENT sans piece de pieces periodiques : doc={document_id}")
            problems += 1
    print("OK : aucun probleme" if problems == 0 else f"{problems} point(s) a verifier")


if __name__ == "__main__":
    main()