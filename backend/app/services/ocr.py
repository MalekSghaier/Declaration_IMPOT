"""Moteur OCR (Mistral). Seul ce fichier connait Mistral : on pourra le remplacer."""
import base64
import logging
import mimetypes
import threading
import time
from pathlib import Path

from mistralai.client import Mistral
from mistralai.client.errors.sdkerror import SDKError

from app.config import settings

logger = logging.getLogger("app.ocr")
logging.basicConfig(level=logging.INFO)

# --- Sérialisation globale des appels OCR ---
# Les limites Mistral sont au niveau du workspace (pas par requête), donc si
# patente + RNE (ou deux utilisateurs) appellent en même temps, on épuise le
# quota plus vite qu'un seul flux séquentiel. Un verrou global + un espacement
# minimum entre deux appels réduisent nettement les 429.
_ocr_lock = threading.Lock()
_last_call_at = 0.0
MIN_INTERVAL_SECONDS = settings.MISTRAL_OCR_MIN_INTERVAL

_client_singleton: Mistral | None = None
_client_lock = threading.Lock()


def _client() -> Mistral:
    # On réutilise un seul client au lieu d'en recréer un par appel.
    global _client_singleton
    if _client_singleton is None:
        with _client_lock:
            if _client_singleton is None:
                _client_singleton = Mistral(
                    api_key=settings.MISTRAL_API_KEY.get_secret_value(),
                    server_url=settings.MISTRAL_BASE_URL,
                )
    return _client_singleton


def _log_rate_limit_headers(exc: SDKError, context: str) -> None:
    """Journalise les en-têtes de limite de débit d'une erreur, sans jamais afficher le contenu du document."""
    for attr in ("raw_response", "http_res", "response", "_raw_response"):
        raw = getattr(exc, attr, None)
        if raw is not None and hasattr(raw, "headers"):
            headers = dict(raw.headers)
            interesting = {k: v for k, v in headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
            logger.info("[OCR][%s] en-tetes de limite : %s", context, interesting or "aucun")
            return
    logger.debug("[OCR][%s] pas d'en-tetes HTTP disponibles sur %s", context, type(exc).__name__)


def _get_retry_after(exc: SDKError) -> float | None:
    raw = getattr(exc, "raw_response", None) or getattr(exc, "http_res", None)
    headers = getattr(raw, "headers", None) if raw is not None else None
    if not headers:
        return None
    value = dict(headers).get("Retry-After") or dict(headers).get("retry-after")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def ocr_file(path: str, page_indexes: list[int] | None = None, max_retries: int = 5) -> dict[int, str]:
    global _last_call_at

    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    b64 = base64.b64encode(p.read_bytes()).decode()
    data_url = f"data:{mime};base64,{b64}"

    if mime == "application/pdf":
        document = {"type": "document_url", "document_url": data_url}
    else:
        document = {"type": "image_url", "image_url": data_url}

    kwargs = {"model": settings.MISTRAL_OCR_MODEL, "document": document}
    if page_indexes is not None and mime == "application/pdf":
        kwargs["pages"] = page_indexes

    wait = 5
    for attempt in range(max_retries):
        with _ocr_lock:
            elapsed = time.monotonic() - _last_call_at
            if elapsed < MIN_INTERVAL_SECONDS:
                time.sleep(MIN_INTERVAL_SECONDS - elapsed)
            try:
                response = _client().ocr.process(**kwargs)
                _last_call_at = time.monotonic()
                return {page.index: page.markdown for page in response.pages}
            except SDKError as exc:
                _last_call_at = time.monotonic()
                _log_rate_limit_headers(exc, context=p.name)
                if "429" in str(exc) and attempt < max_retries - 1:
                    retry_after = _get_retry_after(exc)
                    delay = retry_after if retry_after is not None else wait
                    logger.warning(
                        "[429] limite atteinte, attente %.1fs (essai %d/%d)...",
                        delay, attempt + 1, max_retries,
                    )
                    time.sleep(delay)
                    wait *= 2
                    continue
                raise