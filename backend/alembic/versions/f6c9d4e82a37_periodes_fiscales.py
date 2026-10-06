"""periodes fiscales (tax_periods) et rattachement documents.tax_period_id

Revision ID: f6c9d4e82a37
Revises: b547ff89984f
Create Date: 2026-10-05 11:32:19.000000

"""
import calendar
import logging
import re
from datetime import date
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6c9d4e82a37'
down_revision: Union[str, Sequence[str], None] = 'b547ff89984f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# Chiffres ASCII uniquement : une valeur en chiffres arabo-indiens n'est jamais reprise.
_MONTH_RE = re.compile(r"[0-9]{4}-[0-9]{2}")


def _month_bounds(value):
    """(premier jour, dernier jour) du mois AAAA-MM, ou None si la valeur n'est pas une vraie date.

    Aucune borne d'annees arbitraire : seule compte la conversion reelle en date.
    """
    if not isinstance(value, str) or not _MONTH_RE.fullmatch(value):
        return None
    year, month = int(value[:4]), int(value[5:7])
    try:
        start = date(year, month, 1)
    except ValueError:
        return None
    return start, date(year, month, calendar.monthrange(year, month)[1])


def _backfill_documents() -> None:
    """Copie : cree les periodes MONTH des pieces existantes et les y rattache.

    Document.period n'est jamais modifiee. Une valeur absente, invalide ou ambigue laisse
    tax_period_id a NULL : aucune periode n'est inventee (voir check_periods.py).
    """
    conn = op.get_bind()
    documents = sa.table(
        'documents',
        sa.column('id', sa.Integer), sa.column('company_id', sa.Integer),
        sa.column('kind', sa.String), sa.column('period', sa.String),
        sa.column('tax_period_id', sa.Integer),
    )
    tax_periods = sa.table(
        'tax_periods',
        sa.column('id', sa.Integer), sa.column('company_id', sa.Integer),
        sa.column('period_type', sa.String), sa.column('period_start', sa.Date),
        sa.column('period_end', sa.Date), sa.column('status', sa.String),
    )
    audit_log = sa.table(
        'audit_log',
        sa.column('company_id', sa.Integer), sa.column('action', sa.String),
        sa.column('object_type', sa.String), sa.column('object_id', sa.Integer),
        sa.column('new_value', sa.JSON), sa.column('reason', sa.String),
    )

    rows = conn.execute(
        sa.select(documents.c.id, documents.c.company_id, documents.c.period)
        .where(documents.c.kind.in_(('FACTURE', 'FICHE_PAIE')))
        .order_by(documents.c.id)
    ).all()

    period_ids: dict = {}  # (company_id, premier jour) -> id de la periode
    attach: dict = {}      # id de la periode -> ids des documents
    skipped: list = []
    for doc_id, company_id, period in rows:
        bounds = _month_bounds(period)
        if bounds is None:
            skipped.append(doc_id)
            continue
        start, end = bounds
        key = (company_id, start)
        if key not in period_ids:
            period_id = conn.execute(
                tax_periods.insert()
                .values(company_id=company_id, period_type='MONTH', period_start=start,
                        period_end=end, status='OPEN')
                .returning(tax_periods.c.id)
            ).scalar_one()
            period_ids[key] = period_id
            conn.execute(
                audit_log.insert().values(
                    company_id=company_id, action='PERIODE_CREEE', object_type='tax_period',
                    object_id=period_id, reason='MIGRATION_INITIALE',
                    new_value={'period_type': 'MONTH', 'period_start': start.isoformat(),
                               'period_end': end.isoformat()},
                )
            )
        attach.setdefault(period_ids[key], []).append(doc_id)

    for period_id, doc_ids in attach.items():
        conn.execute(documents.update().where(documents.c.id.in_(doc_ids)).values(tax_period_id=period_id))

    if skipped:
        log.warning(
            "%d document(s) sans periode fiscale rattachee (periode absente ou invalide), ids : %s. "
            "Lancez check_periods.py pour les verifier.",
            len(skipped), skipped[:20],
        )


def upgrade() -> None:
    """Upgrade schema (additif : aucune colonne existante n'est modifiee)."""
    op.create_table(
        'tax_periods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('period_type', sa.String(length=20), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=30), server_default='OPEN', nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('period_end >= period_start', name='ck_tax_period_dates'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'period_type', 'period_start', name='uq_tax_period_company_type_start'),
    )

    op.add_column('documents', sa.Column('tax_period_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_documents_tax_period_id', 'documents', 'tax_periods', ['tax_period_id'], ['id'])
    op.create_index('ix_documents_tax_period', 'documents', ['tax_period_id'], unique=False)

    _backfill_documents()


def downgrade() -> None:
    """Downgrade schema.

    Retire le rattachement et la table des periodes. Les periodes de la reprise se recalculent depuis
    Document.period (jamais modifiee) ; celles creees ensuite a la main sont perdues. Les lignes
    d'audit PERIODE_CREEE restent.
    """
    op.drop_index('ix_documents_tax_period', table_name='documents')
    op.drop_constraint('fk_documents_tax_period_id', 'documents', type_='foreignkey')
    op.drop_column('documents', 'tax_period_id')
    op.drop_table('tax_periods')