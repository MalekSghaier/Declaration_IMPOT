import logging
import os
import random
import re
import tempfile
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pymupdf
from pydantic import BaseModel

from app import storage
from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.services.document_reader import read_document
from app.services.extraction import extract_invoice, extract_payslip
from app.services.schemas import InvoiceExtraction, PayslipExtraction, TvaLine
from app.services.validation import validate_invoice, validate_payslip
from app.models import Company, Document
from app.services.classification import classify_direction

logger = logging.getLogger("app.tasks")

# Donnees fictives utilisees uniquement quand OCR_MOCK=true
MOCK_INVOICE = InvoiceExtraction(
    numero="FAC-MOCK-001",
    date_facture=date(2025, 9, 15),
    emetteur_nom="FOURNISSEUR EXEMPLE",
    emetteur_mf="7654321B/A/M/000",
    client_nom="SOCIETE EXEMPLE",
    client_mf="1234567A",
    lignes_tva=[TvaLine(taux=19.0, base_ht=100.0, montant_tva=19.0)],
    total_ht=100.0,
    total_tva=19.0,
    timbre=1.0,
    total_ttc=120.0,
    devise="TND",
)

MOCK_PAYSLIP = PayslipExtraction(
    salarie_nom="SALARIE EXEMPLE",
    mois=date.today().strftime("%Y-%m"),
    salaire_brut=2000.0,
    cnss_salariale=183.6,
    salaire_imposable=1816.4,
    retenue_irpp=320.0,
    css=8.3,
    net_a_payer=1488.1,
)

# Erreurs qui valent la peine d'etre retentees : reseau, delai depasse, limite de debit, erreur serveur
_TRANSIENT_RE = re.compile(
    r"\b(429|500|502|503|504)\b|timeout|timed out|connection|temporar|max retries exceeded",
    re.IGNORECASE,
)


@celery_app.task(name="ping")
def ping(name: str = "monde") -> str:
    return f"pong {name}"


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return bool(_TRANSIENT_RE.search(str(exc)))


def _short_error(exc: Exception, retries: int, transient: bool) -> str:
    text = f"{type(exc).__name__}: {exc}"[:450]
    if transient:
        text += f" (abandon apres {retries + 1} tentatives)"
    return text


def _safe_unlink(path: str) -> None:
    """Sous Windows le fichier peut rester verrouille apres une erreur de lecture : on ne fait pas echouer la tache."""
    try:
        os.unlink(path)
    except OSError:
        logger.warning("fichier temporaire non supprime : %s", path)


def _read_pages(doc: Document, raw: bytes) -> tuple[list, str]:
    """Ecrit le fichier dans un temp, le lit (natif ou OCR), renvoie (pages, methode)."""
    ext = Path(doc.minio_key).suffix.lower()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        pages = read_document(tmp_path)
    except pymupdf.FileDataError as exc:
        raise ValueError("fichier illisible ou corrompu") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            _safe_unlink(tmp_path)

    if not any(p.text.strip() for p in pages):
        raise ValueError("aucun texte lisible dans le document")

    method = "ocr" if any(p.method == "ocr" for p in pages) else "native"
    return pages, method


def _extract_fields(doc: Document, raw: bytes) -> tuple[BaseModel, str]:
    """Dispatch par type de document : facture ou fiche de paie."""
    if settings.OCR_MOCK:
        logger.warning("[MOCK] extraction simulee pour le document %s", doc.id)
        if doc.kind == "FACTURE":
            return MOCK_INVOICE, "mock"
        return MOCK_PAYSLIP, "mock"

    pages, method = _read_pages(doc, raw)
    if doc.kind == "FACTURE":
        return extract_invoice(pages), method
    if doc.kind == "FICHE_PAIE":
        return extract_payslip(pages), method
    raise ValueError(f"type de document non supporte : {doc.kind}")


@celery_app.task(
    name="process_piece",
    bind=True,
    max_retries=settings.PIECES_MAX_RETRIES,
    rate_limit=settings.PIECES_RATE_LIMIT,
    ignore_result=True,
)
def process_piece(self, document_id: int) -> None:
    """Traite une piece (facture ou fiche de paie) : lecture, extraction, verification."""
    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if doc is None:
            logger.warning("document %s introuvable", document_id)
            return
        if doc.kind not in ("FACTURE", "FICHE_PAIE"):
            logger.info("document %s ignore (type %s)", doc.id, doc.kind)
            return
        if doc.processing_status not in ("UPLOADED", "PROCESSING"):
            logger.info("document %s ignore (statut %s)", doc.id, doc.processing_status)
            return

        doc.processing_status = "PROCESSING"
        doc.error = None
        db.commit()

        try:
            raw = storage.get_bytes(doc.minio_key)
            fields, method = _extract_fields(doc, raw)
        except Exception as exc:
            retries = self.request.retries
            transient = _is_transient(exc)
            if transient and retries < self.max_retries:
                delay = settings.PIECES_RETRY_BASE_SECONDS * (2**retries) + random.randint(0, 10)
                logger.warning(
                    "document %s : erreur temporaire (%s), nouvelle tentative dans %ss", doc.id, exc, delay
                )
                raise self.retry(exc=exc, countdown=delay)
            logger.exception("document %s : echec definitif", doc.id)
            doc.processing_status = "FAILED"
            doc.error = _short_error(exc, retries, transient)
            db.commit()
            return

        company = db.get(Company, doc.company_id)
        company_mf = company.matricule_fiscal if company else None

        # --- Validation et enrichissement selon le type ---
        if doc.kind == "FACTURE":
            # Mode simulation : la societe est le client, pour obtenir un ACHAT coherent
            if method == "mock":
                fields = fields.model_copy(update={"client_mf": company_mf})
            issues = validate_invoice(fields, doc.period)
            direction, class_issues = classify_direction(fields.emetteur_mf, fields.client_mf, company_mf)
            issues += class_issues
            doc.direction = direction
        else:  # FICHE_PAIE
            issues = validate_payslip(fields, doc.period)
            # Pas de notion vente/achat pour une fiche de paie
            doc.direction = None

        doc.extracted_data = {
            "fields": fields.model_dump(mode="json"),
            "issues": [asdict(i) for i in issues],
            "method": method,
        }
        doc.processing_status = "NEEDS_REVIEW" if issues else "EXTRACTED"
        db.commit()
        logger.info("document %s (%s) : %s (%d alerte(s))", doc.id, doc.kind, doc.processing_status, len(issues))
    finally:
        db.close()