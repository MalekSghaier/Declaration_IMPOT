from sqlalchemy import text
from app.database import SessionLocal

with SessionLocal() as db:
    print("pieces :", db.execute(text(
        "select kind, processing_status, count(*) from documents group by 1, 2 order by 1, 2")).all())
    print("snapshots :", db.execute(text(
        "select status, normalizer_version, count(*) from document_snapshots group by 1, 2")).all())
    print("FK documents :", db.execute(text(
        "select conname, pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = 'documents'::regclass and contype = 'f'")).all())
    print("documents/periodes de societes differentes :", db.execute(text(
        "select count(*) from documents d join tax_periods t on t.id = d.tax_period_id "
        "where t.company_id <> d.company_id")).all())