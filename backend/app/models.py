from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(default="ONBOARDING")  # ONBOARDING | LOCKED
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

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
    extracted_data: Mapped[dict | None] = mapped_column(JSON)
    # Onboarding : DONE... / Factures et fiches de paie : UPLOADED | EXTRACTED | NEEDS_REVIEW | VALIDATED | FAILED
    processing_status: Mapped[str] = mapped_column(default="DONE")
    error: Mapped[str | None]
    period: Mapped[str | None] = mapped_column(String(7))  # AAAA-MM, ex. 2025-09
    direction: Mapped[str | None]  # VENTE | ACHAT (factures uniquement)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())