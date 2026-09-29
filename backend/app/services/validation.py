import re
from dataclasses import dataclass
from decimal import Decimal

from app.services.schemas import InvoiceExtraction

TAUX_VALIDES = {Decimal(x) for x in (0, 7, 13, 19)}
TOLERANCE = Decimal("0.005")
DEVISES_DINAR = {"TND", "DT", "DINAR", "DINARS"}
# A affiner avec vos vrais documents
MF_REGEX = re.compile(r"^\d{7}\s*[A-Z](\s*/\s*[A-Z]\s*/\s*[A-Z]\s*/\s*\d{3})?$")


@dataclass
class Issue:
    field: str
    message: str
    severity: str  # "error" ou "warning"


def _d(value: float | None) -> Decimal | None:
    """Les montants arrivent du LLM en float : on calcule en Decimal (str() evite les erreurs binaires)."""
    return None if value is None else Decimal(str(value))


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE


def validate_invoice(inv: InvoiceExtraction, period: str | None = None) -> list[Issue]:
    issues: list[Issue] = []

    for field in ("numero", "date_facture", "total_ht", "total_tva", "total_ttc"):
        if getattr(inv, field) is None:
            issues.append(Issue(field, "champ manquant", "error"))

    if inv.date_facture is not None and period and inv.date_facture.strftime("%Y-%m") != period:
        issues.append(Issue("date_facture", f"date hors de la periode {period}", "warning"))

    total_ht, total_tva, total_ttc, timbre = _d(inv.total_ht), _d(inv.total_tva), _d(inv.total_ttc), _d(inv.timbre)

    if total_ht is not None and total_tva is not None and total_ttc is not None:
        attendu = total_ht + total_tva + (timbre or Decimal(0))
        if not _close(attendu, total_ttc):
            issues.append(Issue("total_ttc", f"HT+TVA+timbre={attendu} != TTC={total_ttc}", "error"))

    lignes = [(_d(l.taux), _d(l.base_ht), _d(l.montant_tva)) for l in inv.lignes_tva]
    for taux, base, montant in lignes:
        if taux not in TAUX_VALIDES:
            issues.append(Issue("lignes_tva", f"taux invalide : {taux}", "error"))
        elif not _close(base * taux / 100, montant):
            issues.append(Issue("lignes_tva", f"base {base} x {taux}% != {montant}", "warning"))

    if lignes:
        if total_tva is not None and not _close(sum(m for _, _, m in lignes), total_tva):
            issues.append(Issue("total_tva", "somme des lignes != total TVA", "error"))
        if total_ht is not None and not _close(sum(b for _, b, _ in lignes), total_ht):
            issues.append(Issue("total_ht", "somme des bases != total HT", "warning"))

    if not inv.emetteur_mf and not inv.client_mf:
        issues.append(Issue("emetteur_mf", "aucun matricule fiscal lu", "warning"))
    for f in ("emetteur_mf", "client_mf"):
        v = getattr(inv, f)
        if v and not MF_REGEX.match(v.strip().upper()):
            issues.append(Issue(f, f"format de matricule fiscal douteux : {v}", "warning"))

    if inv.devise:
        code = re.sub(r"[^A-Z]", "", inv.devise.upper())
        if code not in DEVISES_DINAR:
            issues.append(Issue("devise", f"devise differente du dinar : {inv.devise}", "warning"))

    return issues