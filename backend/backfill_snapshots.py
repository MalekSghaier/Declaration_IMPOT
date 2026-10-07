"""Backfill des snapshots pour les pieces deja VALIDATED. Idempotent.

Depuis backend\\ (venv active) :
  python backfill_snapshots.py --dry-run   (n'ecrit rien)
  python backfill_snapshots.py

Utilise exactement la meme fonction que la validation (app.snapshots.create_or_confirm_snapshot) avec
origin=BACKFILL. N'invente jamais d'original_data. Une piece VALIDATED dont le snapshot courant a une
empreinte differente est SIGNALEE, jamais corrigee automatiquement.
"""
import argparse

from sqlalchemy import select

from app.audit import log_action
from app.database import SessionLocal
from app.intervals import StructureError
from app.models import AuditLog, Document
from app.normalization import CATALOG
from app.snapshots import (
    canonical_fields, compute_fingerprint, create_or_confirm_snapshot, current_snapshot,
)


def last_validation(db, doc):
    return db.scalar(
        select(AuditLog)
        .where(
            AuditLog.company_id == doc.company_id,
            AuditLog.object_type == "document",
            AuditLog.object_id == doc.id,
            AuditLog.action == "PIECE_VALIDEE",
        )
        .order_by(AuditLog.id.desc())
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill des snapshots des pieces VALIDATED")
    parser.add_argument("--dry-run", action="store_true", help="n'ecrit rien, affiche seulement")
    args = parser.parse_args()

    counts = {"CREE": 0, "DEJA_OK": 0, "DIVERGENT": 0, "INVALIDE": 0}
    with SessionLocal() as db:
        docs = db.scalars(
            select(Document)
            .where(Document.kind.in_(tuple(CATALOG)), Document.processing_status == "VALIDATED")
            .order_by(Document.id)
        ).all()
        for doc in docs:
            payload = doc.extracted_data or {}
            try:
                fields = canonical_fields(doc.kind, payload.get("fields") or {})
            except StructureError as exc:
                counts["INVALIDE"] += 1
                print(f"INVALIDE   doc={doc.id} : {exc}")
                continue

            current = current_snapshot(db, doc.id, doc.company_id)
            if current is not None:
                same = current.fingerprint == compute_fingerprint(doc.kind, doc.direction, doc.tax_period_id, fields)
                counts["DEJA_OK" if same else "DIVERGENT"] += 1
                if not same:
                    print(f"DIVERGENT  doc={doc.id} : le snapshot courant (v{current.version}) differe de la piece")
                continue

            if args.dry_run:
                counts["CREE"] += 1
                print(f"A CREER    doc={doc.id} (societe {doc.company_id}, periode {doc.tax_period_id})")
                continue

            audit = last_validation(db, doc)
            result = create_or_confirm_snapshot(
                db, doc, fields, audit.user_id if audit else None,
                origin="BACKFILL",
                extraction_method=payload.get("method"),
                issues=payload.get("issues"),
                validated_at=audit.created_at if audit else None,
            )
            log_action(
                db, doc.company_id, None, "SNAPSHOT_CREE_BACKFILL", "document", doc.id,
                new={"snapshot_id": result.snapshot.id, "version": result.snapshot.version,
                     "fingerprint": result.snapshot.fingerprint},
            )
            db.commit()
            counts["CREE"] += 1
            print(f"CREE       doc={doc.id} -> snapshot {result.snapshot.id} v{result.snapshot.version}")
    print(counts)


if __name__ == "__main__":
    main()