"""Classification vente / achat d'une facture, par comparaison des matricules fiscaux."""
import re

from app.services.validation import Issue

# 7 chiffres + lettre de cle. Les codes TVA / categorie / etablissement sont ignores :
# ils sont souvent omis sur les factures.
_MF_RE = re.compile(r"(?<!\d)(\d{7})([A-Z])")


def normalize_mf(value: str | None) -> str | None:
    """'1234567/A/M/000', '1234567 a' et '1234567A' donnent tous '1234567A'. None si illisible."""
    if not value:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    m = _MF_RE.search(cleaned)
    return m.group(1) + m.group(2) if m else None


def classify_direction(
    emetteur_mf: str | None, client_mf: str | None, company_mf: str | None
) -> tuple[str | None, list[Issue]]:
    """Renvoie (direction, alertes). direction vaut 'VENTE', 'ACHAT' ou None si on ne doit pas deviner."""
    company = normalize_mf(company_mf)
    if company is None:
        return None, [Issue("direction", "matricule fiscal de la societe illisible : classification impossible", "warning")]

    is_emetteur = normalize_mf(emetteur_mf) == company
    is_client = normalize_mf(client_mf) == company

    if is_emetteur and is_client:
        return None, [Issue("direction", "la societe apparait comme emetteur ET client : incoherent", "error")]
    if is_emetteur:
        return "VENTE", []
    if is_client:
        return "ACHAT", []
    return None, [Issue("direction", "ni l'emetteur ni le client ne correspond a la societe : choisir vente ou achat", "warning")]