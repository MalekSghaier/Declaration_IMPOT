"""Gestion des valeurs de reference (table reference_values), sans migration.

Depuis le dossier backend\\ (venv active) :
  python reference_cli.py list [CATEGORIE]
  python reference_cli.py add CATEGORIE CODE "Libelle" [--alias "Autre libelle"]... [--status CONFIRMED --source "..." --source-ref "..."]
  python reference_cli.py confirm CATEGORIE CODE --source "..." --source-ref "..."
  python reference_cli.py delete CATEGORIE CODE

Une valeur ajoutee est NEEDS_VERIFICATION par defaut. Elle ne passe CONFIRMED que si une source
officielle (--source) et sa reference (--source-ref) sont indiquees.
"""
import argparse
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ReferenceValue
from app.reference import CATEGORY_RE


def _category(raw: str) -> str:
    category = raw.strip().upper()
    if not CATEGORY_RE.match(category):
        sys.exit(f"Categorie invalide : {raw!r} (lettres majuscules, chiffres et _ ; 40 caracteres max)")
    return category


def _find(db, category: str, code: str) -> ReferenceValue | None:
    return db.scalar(
        select(ReferenceValue).where(ReferenceValue.category == category, ReferenceValue.code == code)
    )


def cmd_list(db, args) -> None:
    stmt = select(ReferenceValue).order_by(ReferenceValue.category, ReferenceValue.code)
    if args.category:
        stmt = stmt.where(ReferenceValue.category == _category(args.category))
    rows = db.scalars(stmt).all()
    if not rows:
        print("(aucune valeur)")
        return
    for r in rows:
        print(
            f"{r.category} | {r.code} | {r.label} | {r.status} | alias={r.aliases or []} "
            f"| source={r.source or '-'} ({r.source_reference or '-'})"
        )


def cmd_add(db, args) -> None:
    category, code = _category(args.category), args.code.strip()
    if not code or len(code) > 64:
        sys.exit("Code vide ou trop long (64 caracteres max)")
    if args.status == "CONFIRMED" and not (args.source and args.source_ref):
        sys.exit("Un statut CONFIRMED exige --source et --source-ref (source officielle)")
    if _find(db, category, code):
        sys.exit(f"{category}/{code} existe deja")
    db.add(
        ReferenceValue(
            category=category,
            code=code,
            label=args.label.strip(),
            aliases=[a.strip() for a in args.alias if a.strip()],
            status=args.status,
            source=args.source,
            source_reference=args.source_ref,
        )
    )
    db.commit()
    print(f"Ajoute : {category}/{code} ({args.status})")


def cmd_confirm(db, args) -> None:
    category, code = _category(args.category), args.code.strip()
    ref = _find(db, category, code)
    if ref is None:
        sys.exit(f"{category}/{code} introuvable")
    ref.status = "CONFIRMED"
    ref.source = args.source
    ref.source_reference = args.source_ref
    db.commit()
    print(f"Confirme : {category}/{code}")


def cmd_delete(db, args) -> None:
    category, code = _category(args.category), args.code.strip()
    ref = _find(db, category, code)
    if ref is None:
        sys.exit(f"{category}/{code} introuvable")
    db.delete(ref)
    db.commit()
    print(f"Supprime : {category}/{code}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gestion des valeurs de reference")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("category", nargs="?")

    p = sub.add_parser("add")
    p.add_argument("category")
    p.add_argument("code")
    p.add_argument("label")
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--status", choices=["NEEDS_VERIFICATION", "CONFIRMED"], default="NEEDS_VERIFICATION")
    p.add_argument("--source")
    p.add_argument("--source-ref", dest="source_ref")

    p = sub.add_parser("confirm")
    p.add_argument("category")
    p.add_argument("code")
    p.add_argument("--source", required=True)
    p.add_argument("--source-ref", dest="source_ref", required=True)

    p = sub.add_parser("delete")
    p.add_argument("category")
    p.add_argument("code")

    args = parser.parse_args()
    handlers = {"list": cmd_list, "add": cmd_add, "confirm": cmd_confirm, "delete": cmd_delete}
    with SessionLocal() as db:
        handlers[args.cmd](db, args)


if __name__ == "__main__":
    main()