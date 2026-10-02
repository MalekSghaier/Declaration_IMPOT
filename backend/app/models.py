from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    """Ancre du contribuable. Les colonnes d'identite ci-dessous sont conservees pour compatibilite :
    le profil fiscal date et trace vit dans profile_values."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(default="ONBOARDING")  # ONBOARDING | LOCKED
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Code d'une valeur de reference de categorie TAXPAYER_TYPE (voir reference_values)
    taxpayer_type: Mapped[str | None] = mapped_column(String(64))

    # Renseigne lors de l'onboarding, verrouille ensuite
    raison_sociale: Mapped[str | None]
    matricule_fiscal: Mapped[str | None]
    adresse: Mapped[str | None]
    forme_juridique: Mapped[str | None]
    code_tva: Mapped[str | None]
    code_categorie: Mapped[str | None]
    capital: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    date_creation: Mapped[date | None] = mapped_column(Date)
    activite: Mapped[str | None]
    dirigeant: Mapped[str | None]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Une seule patente et un seul RNE par societe
        Index(
            "uq_company_onboarding_kind",
            "company_id",
            "kind",
            unique=True,
            postgresql_where=text("kind IN ('PATENTE', 'RNE')"),
        ),
        # Un meme fichier (meme SHA-256) ne peut pas etre depose deux fois par societe
        Index(
            "uq_company_sha256_pieces",
            "company_id",
            "sha256",
            unique=True,
            postgresql_where=text("kind IN ('FACTURE', 'FICHE_PAIE')"),
        ),
        Index("ix_documents_company_period", "company_id", "period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[str]  # PATENTE | RNE | FACTURE | FICHE_PAIE
    minio_key: Mapped[str]
    filename: Mapped[str]
    sha256: Mapped[str]
    # Version courante (corrigee par l'utilisateur)
    extracted_data: Mapped[dict | None] = mapped_column(JSON)
    # Sortie brute de l'extraction automatique, jamais modifiee ensuite
    original_data: Mapped[dict | None] = mapped_column(JSON)
    # Onboarding : DONE... / Factures et fiches de paie : UPLOADED | PROCESSING | EXTRACTED | NEEDS_REVIEW | VALIDATED | REJECTED | FAILED
    processing_status: Mapped[str] = mapped_column(default="DONE")
    error: Mapped[str | None]
    period: Mapped[str | None] = mapped_column(String(7))  # AAAA-MM, ex. 2025-09
    direction: Mapped[str | None]  # VENTE | ACHAT (factures uniquement)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractedField(Base):
    """Un champ extrait d'un document (RNE, patente, facture, fiche de paie).

    Contrairement a profile_values, cette table est propre a UN document : elle
    represente ce que la lecture de ce document a produit pour un champ donne,
    avant toute consolidation dans le profil du contribuable.

    - value : valeur du champ, en JSON (str, nombre, date au format ISO, liste).
      Nullable : une extraction peut echouer sur un champ precis sans echouer sur les autres.
    - page : page d'ou le champ a ete lu (NULL si non applicable, ex. fiche de paie).
    - confidence : niveau de confiance [0, 1] (NULL si non mesure). Contrainte en base.
    - status : EXTRACTED | CORRECTED | CONFIRMED | UNCERTAIN (meme liste que profile_values).
      Une extraction automatique ne doit PAS ecraser un champ CORRECTED ou CONFIRMED ;
      cette regle est portee par app.document_fields.
    - extraction_method : "native" | "ocr" | "mock" (NULL si inconnu).

    Note V1 : les sous-champs de lignes_tva (facture) ne sont PAS des ExtractedField
    independants. lignes_tva est stockee comme un seul champ JSON.
    """

    __tablename__ = "extracted_fields"
    __table_args__ = (
        Index("ix_extracted_fields_document", "document_id"),
        UniqueConstraint("document_id", "field_code", name="uq_extracted_field_document_code"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_extracted_field_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    field_code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    page: Mapped[int | None]
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(20), default="EXTRACTED", nullable=False)
    extraction_method: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuditLog(Base):
    """Journal des modifications importantes : qui a change quoi, quand, et l'ancienne valeur."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_company_object", "company_id", "object_type", "object_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str]  # ex. PIECE_CORRIGEE, PIECE_VALIDEE, PIECE_REJETEE, SOCIETE_CONFIRMEE
    object_type: Mapped[str]  # ex. document, company
    object_id: Mapped[int | None]
    field: Mapped[str | None]
    old_value: Mapped[dict | list | str | int | float | None] = mapped_column(JSON)
    new_value: Mapped[dict | list | str | int | float | None] = mapped_column(JSON)
    reason: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReferenceValue(Base):
    """Valeur d'une liste de reference (TAXPAYER_TYPE, LEGAL_FORM, TAX_REGIME...).

    Les categories et les valeurs sont des DONNEES : en ajouter ne demande aucune migration.
    status : NEEDS_VERIFICATION (defaut) ou CONFIRMED (source officielle renseignee).
    valid_from / valid_to : [valid_from, valid_to[, NULL = ouvert.
    aliases : liste de libelles equivalents reconnus a l'extraction.
    """

    __tablename__ = "reference_values"
    __table_args__ = (UniqueConstraint("category", "code", name="uq_reference_category_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str]
    aliases: Mapped[list | None] = mapped_column(JSON)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None]
    source_reference: Mapped[str | None]
    status: Mapped[str] = mapped_column(
        String(20), default="NEEDS_VERIFICATION", server_default="NEEDS_VERIFICATION"
    )
    extra: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileValue(Base):
    """Une information du profil d'un contribuable, pendant une periode [valid_from, valid_to[.

    valid_from NULL = debut inconnu. valid_to NULL = version en cours (une seule par societe et par code).
    attribute_code n'est pas une liste fermee : toute information peut etre ajoutee sans migration.
    status : EXTRACTED | CORRECTED | CONFIRMED | UNCERTAIN.
    """

    __tablename__ = "profile_values"
    __table_args__ = (
        Index("ix_profile_company_attr", "company_id", "attribute_code"),
        Index(
            "uq_profile_value_current",
            "company_id",
            "attribute_code",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
            sqlite_where=text("valid_to IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    attribute_code: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict | list | str | int | float | bool] = mapped_column(JSON, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED")
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    # Lien vers la ligne extracted_fields qui a fourni la valeur confirmee (tracabilite)
    extracted_field_id: Mapped[int | None] = mapped_column(ForeignKey("extracted_fields.id"))
    page: Mapped[int | None]
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reason: Mapped[str | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Activity(Base):
    """Activite d'un contribuable pendant [valid_from, valid_to[ (NULL = inconnu / en cours).

    Plusieurs activites possibles ; au plus une principale par periode (verifie par le service,
    et par un index unique pour la version en cours).
    code : code de nomenclature, NULL tant qu'une nomenclature officielle n'est pas confirmee.
    """

    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_company", "company_id"),
        Index(
            "uq_activity_primary_current",
            "company_id",
            unique=True,
            postgresql_where=text("is_primary AND valid_to IS NULL"),
            sqlite_where=text("is_primary AND valid_to IS NULL"),
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_activity_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    label: Mapped[str]
    code: Mapped[str | None] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(default=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED")
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    page: Mapped[int | None]
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reason: Mapped[str | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Establishment(Base):
    """Etablissement (siege ou secondaire) pendant [valid_from, valid_to[.

    number : numero d'etablissement tel que fourni, SANS regle codee en dur.
    number_status : NEEDS_VERIFICATION (defaut) ou CONFIRMED.
    is_head_office est independant du numero.
    """

    __tablename__ = "establishments"
    __table_args__ = (
        Index("ix_establishments_company", "company_id"),
        Index(
            "uq_establishment_head_current",
            "company_id",
            unique=True,
            postgresql_where=text("is_head_office AND valid_to IS NULL"),
            sqlite_where=text("is_head_office AND valid_to IS NULL"),
        ),
        Index(
            "uq_establishment_number_current",
            "company_id",
            "number",
            unique=True,
            postgresql_where=text("number IS NOT NULL AND valid_to IS NULL"),
            sqlite_where=text("number IS NOT NULL AND valid_to IS NULL"),
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_establishment_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    number: Mapped[str | None] = mapped_column(String(10))
    number_status: Mapped[str] = mapped_column(String(20), default="NEEDS_VERIFICATION")
    is_head_office: Mapped[bool] = mapped_column(default=False)
    address: Mapped[str]
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED")
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    page: Mapped[int | None]
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reason: Mapped[str | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())