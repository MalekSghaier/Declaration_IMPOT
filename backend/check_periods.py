"""Verification en LECTURE SEULE du rattachement des documents aux periodes fiscales.

Depuis le dossier backend\\ (venv active) : python check_periods.py
N'ecrit rien. Liste (1) les pieces sans periode rattachee, (2) les rattachements incoherents
avec Document.period.
"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Document, TaxPeriod
from app.tax_period_service import parse_month


def main() -> None:
    problems = 0
    with SessionLocal() as db:
        orphans = db.scalars(
            select(Document)
            .where(Document.kind.in_(("FACTURE", "FICHE_PAIE")), Document.tax_period_id.is_(None))
            .order_by(Document.id)
        ).all()
        for d in orphans:
            if d.period is None:
                why = "periode absente"
            elif parse_month(d.period) is None:
                why = f"periode invalide : {d.period!r}"
            else:
                why = f"periode {d.period!r} valide mais non rattachee"
            print(f"SANS PERIODE  doc={d.id} societe={d.company_id} kind={d.kind} : {why}")
            problems += 1

        linked = db.execute(
            select(Document, TaxPeriod)
            .join(TaxPeriod, Document.tax_period_id == TaxPeriod.id)
            .order_by(Document.id)
        ).all()
        for d, p in linked:
            bounds = parse_month(d.period)
            if p.company_id != d.company_id or p.period_type != "MONTH" or bounds != (p.period_start, p.period_end):
                print(
                    f"INCOHERENT    doc={d.id} period={d.period!r} -> periode {p.id} "
                    f"({p.period_type} {p.period_start} -> {p.period_end}, societe {p.company_id})"
                )
                problems += 1
    print("OK : aucun probleme" if problems == 0 else f"{problems} point(s) a verifier")


if __name__ == "__main__":
    main()