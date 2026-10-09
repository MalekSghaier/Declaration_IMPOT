"""Normalisation deterministe des champs valides d'une piece (niveau NORMALIZED).

Fonctions pures : pas de base de donnees, pas de LLM, pas d'horloge. Meme entree, meme sortie.
AUCUNE regle fiscale ici : pas de taux admis, pas de seuil, pas de qualification. Ce module convertit
des valeurs vers des types exploitables et dit si elles sont fiables. Les controles de coherence
restent dans app.services.validation (non dupliques).

Principes :
- absent (null ou chaine vide) -> ABSENT, jamais zero ;
- invalide, ambigu ou non fini -> UNRELIABLE avec un code de raison, jamais devine
  (une date n'est lue qu'en ISO AAAA-MM-JJ : JJ/MM et MM/JJ sont ambigus) ;
- montants : Decimal(str(x)), aucun arrondi, aucune echelle imposee ;
- aucune unite par defaut et aucun code de devise connu ici : la devise est RESOLUE par l'appelant
  (liste de reference CURRENCY) et transmise via `resolved`.
  Devise presente mais non resolue (inconnue, ambigue, absente de `resolved`) : le fait devise et
  tous les montants du document deviennent UNRELIABLE (fail closed) ;
  devise absente : fait ABSENT, montants fiables sans unite (NULL) ;
  "%" pour un taux est une convention de representation du schema actuel, pas une regle.

Le catalogue decrit, par type de piece, les faits attendus : ajouter un type de piece = ajouter une
entree, sans migration. Tout changement de comportement exige de changer NORMALIZER_VERSION.
"""
import datetime as dt
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.intervals import StructureError
from app.tax_period_service import parse_month

NORMALIZER_VERSION = "2"

AMOUNT, RATE, DATE, MONTH, TEXT, CODE = "AMOUNT", "RATE", "DATE", "MONTH", "TEXT", "CODE"
RELIABLE, UNRELIABLE, ABSENT = "RELIABLE", "UNRELIABLE", "ABSENT"

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Parsed:
    reliability: str
    reason: str | None = None
    value_decimal: Decimal | None = None
    value_date: dt.date | None = None
    value_text: str | None = None


def _absent() -> Parsed:
    return Parsed(ABSENT)


def _bad(reason: str) -> Parsed:
    return Parsed(UNRELIABLE, reason)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def parse_amount(value: Any) -> Parsed:
    if _is_blank(value):
        return _absent()
    if isinstance(value, bool):
        return _bad("NOT_A_NUMBER")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return _bad("NOT_FINITE")
        number = Decimal(str(value))
    else:
        return _bad("NOT_A_NUMBER")
    if not number.is_finite():
        return _bad("NOT_FINITE")
    return Parsed(RELIABLE, value_decimal=number)


def parse_date(value: Any) -> Parsed:
    if _is_blank(value):
        return _absent()
    if isinstance(value, dt.datetime):
        return _bad("NOT_A_DATE")
    if isinstance(value, dt.date):
        return Parsed(RELIABLE, value_date=value)
    if not isinstance(value, str):
        return _bad("NOT_A_DATE")
    text = value.strip()
    if not _ISO_DATE.fullmatch(text):
        return _bad("INVALID_DATE")
    try:
        return Parsed(RELIABLE, value_date=dt.date.fromisoformat(text))
    except ValueError:
        return _bad("INVALID_DATE")


def parse_month_value(value: Any) -> Parsed:
    """Mois AAAA-MM : stocke comme premier jour du mois."""
    if _is_blank(value):
        return _absent()
    if not isinstance(value, str):
        return _bad("NOT_A_MONTH")
    bounds = parse_month(value.strip())
    if bounds is None:
        return _bad("INVALID_MONTH")
    return Parsed(RELIABLE, value_date=bounds[0])


def parse_text(value: Any) -> Parsed:
    if value is None:
        return _absent()
    if not isinstance(value, str):
        return _bad("NOT_TEXT")
    text = " ".join(value.split())
    return Parsed(RELIABLE, value_text=text) if text else _absent()


def parse_code(value: Any) -> Parsed:
    """Identifiant (matricule fiscal...) : espaces retires, majuscules. Aucun controle de structure
    (format officiel non confirme, NEEDS_VERIFICATION)."""
    if value is None:
        return _absent()
    if not isinstance(value, str):
        return _bad("NOT_TEXT")
    text = "".join(value.split()).upper()
    return Parsed(RELIABLE, value_text=text) if text else _absent()


_PARSERS = {
    AMOUNT: parse_amount,
    RATE: parse_amount,
    DATE: parse_date,
    MONTH: parse_month_value,
    TEXT: parse_text,
    CODE: parse_code,
}


@dataclass(frozen=True)
class FactSpec:
    code: str
    value_type: str
    key: str | None = None  # cle dans les champs sources (par defaut : code)


@dataclass(frozen=True)
class KindSpec:
    header: tuple[FactSpec, ...]
    collections: dict[str, tuple[FactSpec, ...]] = field(default_factory=dict)
    # Fait portant la devise du document (unite des montants). Sa valeur est resolue par l'appelant
    # (liste de reference CURRENCY) et transmise a normalize_document via `resolved`.
    currency_fact: str | None = None


CATALOG: dict[str, KindSpec] = {
    "FACTURE": KindSpec(
        header=(
            FactSpec("numero", TEXT),
            FactSpec("date_facture", DATE),
            FactSpec("emetteur_nom", TEXT),
            FactSpec("emetteur_mf", CODE),
            FactSpec("client_nom", TEXT),
            FactSpec("client_mf", CODE),
            FactSpec("total_ht", AMOUNT),
            FactSpec("total_tva", AMOUNT),
            FactSpec("timbre", AMOUNT),
            FactSpec("total_ttc", AMOUNT),
            FactSpec("devise", CODE),
        ),
        collections={
            "lignes_tva": (
                FactSpec("taux", RATE),
                FactSpec("base_ht", AMOUNT),
                FactSpec("montant_tva", AMOUNT),
            )
        },
        currency_fact="devise",
    ),
    "FICHE_PAIE": KindSpec(
        header=(
            FactSpec("salarie_nom", TEXT),
            FactSpec("mois", MONTH),
            FactSpec("salaire_brut", AMOUNT),
            FactSpec("cnss_salariale", AMOUNT),
            FactSpec("salaire_imposable", AMOUNT),
            FactSpec("retenue_irpp", AMOUNT),
            FactSpec("css", AMOUNT),
            FactSpec("net_a_payer", AMOUNT),
        ),
        # Aucune devise dans le schema d'une fiche de paie : unite NULL, jamais de devise supposee.
    ),
}


@dataclass(frozen=True)
class NormalizedFact:
    collection: str
    item_index: int
    fact_code: str
    value_type: str
    value_decimal: Decimal | None
    value_date: dt.date | None
    value_text: str | None
    unit: str | None
    reliability: str
    reason_code: str | None
    raw_value: Any
    source_path: str


def currency_inputs(kind: str, fields: dict) -> dict[str, Any]:
    """Valeurs brutes de devise a resoudre par l'appelant : {fact_code: valeur}.
    Vide si le type de piece n'a pas de devise ou si la devise est absente (rien a resoudre)."""
    spec = CATALOG.get(kind)
    if spec is None or not spec.currency_fact or not isinstance(fields, dict):
        return {}
    currency_spec = next(s for s in spec.header if s.code == spec.currency_fact)
    raw = fields.get(currency_spec.key or currency_spec.code)
    if _is_blank(raw):
        return {}
    return {spec.currency_fact: raw}


def _unit(value_type: str, currency: str | None) -> str | None:
    if value_type == AMOUNT:
        return currency
    if value_type == RATE:
        return "%"
    return None


def _build(
    spec: FactSpec, raw: Any, collection: str, index: int, path: str, currency: str | None,
    forced: Parsed | None = None, blocked: bool = False,
) -> NormalizedFact:
    parsed = forced if forced is not None else _PARSERS[spec.value_type](raw)
    # Devise presente mais non resolue : un montant sans unite connue ne doit jamais etre utilisable.
    if forced is None and blocked and spec.value_type == AMOUNT and parsed.reliability == RELIABLE:
        parsed = _bad("CURRENCY_UNRESOLVED")
    reliable = parsed.reliability == RELIABLE
    return NormalizedFact(
        collection=collection,
        item_index=index,
        fact_code=spec.code,
        value_type=spec.value_type,
        value_decimal=parsed.value_decimal,
        value_date=parsed.value_date,
        value_text=parsed.value_text,
        unit=_unit(spec.value_type, currency) if reliable else None,
        reliability=parsed.reliability,
        reason_code=parsed.reason if parsed.reliability == UNRELIABLE else None,
        raw_value=raw,
        source_path=path,
    )


def normalize_document(
    kind: str, fields: dict, *, resolved: Mapping[str, Parsed] | None = None
) -> list[NormalizedFact]:
    """Faits normalises d'une piece. Ne leve jamais pour une valeur illisible (elle devient UNRELIABLE) ;
    leve StructureError seulement pour un type de piece inconnu ou des champs qui ne sont pas un objet.

    `resolved` : devises resolues par l'appelant, {fact_code: Parsed}. Sans entree pour une devise
    presente, le fait devient UNRELIABLE (CURRENCY_NOT_RESOLVED)."""
    spec = CATALOG.get(kind)
    if spec is None:
        raise StructureError("NORMALIZATION_KIND_UNSUPPORTED", f"type de piece non supporte : {kind!r}")
    if not isinstance(fields, dict):
        raise StructureError("NORMALIZATION_INPUT_INVALID", "les champs valides doivent etre un objet")

    currency_code = spec.currency_fact
    currency_parsed: Parsed | None = None
    if currency_code:
        currency_spec = next(s for s in spec.header if s.code == currency_code)
        raw_currency = fields.get(currency_spec.key or currency_spec.code)
        if _is_blank(raw_currency):
            currency_parsed = _absent()
        elif resolved is not None and currency_code in resolved:
            currency_parsed = resolved[currency_code]
        else:
            currency_parsed = _bad("CURRENCY_NOT_RESOLVED")
    currency = (
        currency_parsed.value_text if currency_parsed is not None and currency_parsed.reliability == RELIABLE else None
    )
    blocked = currency_parsed is not None and currency_parsed.reliability == UNRELIABLE

    facts: list[NormalizedFact] = []
    for s in spec.header:
        key = s.key or s.code
        if s.code == currency_code:
            facts.append(_build(s, fields.get(key), "", 0, key, currency, forced=currency_parsed))
        else:
            facts.append(_build(s, fields.get(key), "", 0, key, currency, blocked=blocked))

    for name, specs in spec.collections.items():
        items = fields.get(name)
        if items is None or items == []:
            continue
        if not isinstance(items, list):
            for s in specs:
                facts.append(_build(s, items, name, 0, name, currency, forced=_bad("NOT_A_LIST")))
            continue
        for i, item in enumerate(items):
            for s in specs:
                key = s.key or s.code
                path = f"{name}[{i}].{key}"
                if not isinstance(item, dict):
                    facts.append(_build(s, item, name, i, path, currency, forced=_bad("NOT_AN_OBJECT")))
                else:
                    facts.append(_build(s, item.get(key), name, i, path, currency, blocked=blocked))
    return facts