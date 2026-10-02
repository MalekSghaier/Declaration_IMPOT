import re
from dataclasses import dataclass
from decimal import Decimal

from app.services.schemas import InvoiceExtraction, PayslipExtraction

TAUX_VALIDES = {Decimal(x) for x in (0, 7, 13, 19)}
TOLERANCE = Decimal("0.005")
DEVISES_DINAR = {"TND", "DT", "DINAR", "DINARS"}
MF_REGEX = re.compile(r"^\d{7,8}\s*/?\s*[A-Z](\s*/\s*[A-Z]){0,2}(\s*/\s*\d{3})?$")


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


def validate_payslip(ps: PayslipExtraction, period: str | None = None) -> list[Issue]:
    """Verifie la coherence d'un bulletin de paie.

    Pas de recalcul de bareme IRPP : on lit ce qui est affiche et on cherche
    uniquement les incoherences evidentes (montants negatifs, net > brut,
    bulletin hors periode).
    """
    issues: list[Issue] = []

    # 1. Champs obligatoires pour la declaration
    for field in ("salaire_brut", "salaire_imposable", "retenue_irpp", "net_a_payer"):
        if getattr(ps, field) is None:
            issues.append(Issue(field, "champ manquant", "error"))

    # 2. Nom et mois : souvent illisibles sur les fiches masquees/scannees
    if ps.salarie_nom is None:
        issues.append(Issue("salarie_nom", "nom du salarie non identifie", "warning"))

    if ps.mois is None:
        issues.append(Issue("mois", "mois du bulletin non identifie", "warning"))
    elif period and ps.mois != period:
        issues.append(Issue("mois", f"bulletin hors de la periode {period}", "warning"))

    # 3. Coherence des montants
    brut = _d(ps.salaire_brut)
    imposable = _d(ps.salaire_imposable)
    cnss = _d(ps.cnss_salariale)
    irpp = _d(ps.retenue_irpp)
    css = _d(ps.css)
    net = _d(ps.net_a_payer)

    if brut is not None and brut <= 0:
        issues.append(Issue("salaire_brut", "salaire brut nul ou negatif", "error"))

    if cnss is not None and brut is not None and cnss > brut:
        issues.append(Issue("cnss_salariale", "CNSS superieure au salaire brut", "error"))

    if irpp is not None and brut is not None and irpp > brut:
        issues.append(Issue("retenue_irpp", "IRPP superieur au salaire brut", "error"))

    if net is not None and brut is not None and net > brut:
        issues.append(Issue("net_a_payer", "net a payer superieur au salaire brut", "error"))

    # imposable = brut - cnss (approximation usuelle, tolerance large car primes/avantages)
    if brut is not None and cnss is not None and imposable is not None:
        attendu = brut - cnss
        if abs(attendu - imposable) > Decimal("5.00"):
            issues.append(Issue(
                "salaire_imposable",
                f"brut - cnss = {attendu} != imposable {imposable}",
                "warning",
            ))

    # net = imposable - irpp - css (tolerance large : primes ajoutees apres impot)
    if net is not None and imposable is not None and irpp is not None:
        attendu = imposable - irpp - (css or Decimal(0))
        if abs(attendu - net) > Decimal("5.00"):
            issues.append(Issue(
                "net_a_payer",
                f"net theorique {attendu} != net affiche {net}",
                "warning",
            ))

    return issues