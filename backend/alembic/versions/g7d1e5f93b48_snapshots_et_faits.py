"""snapshots de pieces validees (document_snapshots) et faits normalises (snapshot_facts)

Revision ID: g7d1e5f93b48
Revises: f6c9d4e82a37
Create Date: 2026-10-06 14:44:56.502197

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7d1e5f93b48'
down_revision: Union[str, Sequence[str], None] = 'f6c9d4e82a37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (additif : aucune colonne existante n'est modifiee, aucune donnee n'est touchee)."""
    # Cibles des cles etrangeres composites (id est deja unique : ces contraintes ne peuvent pas echouer)
    op.create_unique_constraint('uq_documents_id_company', 'documents', ['id', 'company_id'])
    op.create_unique_constraint('uq_tax_periods_id_company', 'tax_periods', ['id', 'company_id'])

    op.create_table(
        'document_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('tax_period_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('validated_fields', sa.JSON(), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('normalizer_version', sa.String(length=20), nullable=False),
        sa.Column('extraction_method', sa.String(length=20), nullable=True),
        sa.Column('issues', sa.JSON(), nullable=True),
        sa.Column('origin', sa.String(length=12), nullable=False),
        sa.Column('validated_by', sa.Integer(), nullable=True),
        sa.Column('validated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_by', sa.Integer(), nullable=True),
        sa.Column('closed_reason', sa.String(length=40), nullable=True),
        sa.CheckConstraint("status IN ('CURRENT', 'SUPERSEDED', 'WITHDRAWN')", name='ck_snapshot_status'),
        sa.CheckConstraint("origin IN ('REVIEW', 'BACKFILL')", name='ck_snapshot_origin'),
        sa.CheckConstraint('version >= 1', name='ck_snapshot_version'),
        sa.CheckConstraint(
            "(status = 'CURRENT' AND closed_at IS NULL) OR (status <> 'CURRENT' AND closed_at IS NOT NULL)",
            name='ck_snapshot_closure',
        ),
        sa.ForeignKeyConstraint(
            ['document_id', 'company_id'], ['documents.id', 'documents.company_id'], name='fk_snapshot_document'
        ),
        sa.ForeignKeyConstraint(
            ['tax_period_id', 'company_id'], ['tax_periods.id', 'tax_periods.company_id'],
            name='fk_snapshot_tax_period',
        ),
        sa.ForeignKeyConstraint(['validated_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['closed_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'version', name='uq_snapshot_document_version'),
    )
    op.create_index(
        'uq_snapshot_current_per_document', 'document_snapshots', ['document_id'],
        unique=True, postgresql_where=sa.text("status = 'CURRENT'"),
    )
    op.create_index(
        'ix_snapshots_company_period_current', 'document_snapshots', ['company_id', 'tax_period_id'],
        unique=False, postgresql_where=sa.text("status = 'CURRENT'"),
    )

    op.create_table(
        'snapshot_facts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('snapshot_id', sa.Integer(), nullable=False),
        sa.Column('collection', sa.String(length=40), nullable=False),
        sa.Column('item_index', sa.Integer(), nullable=False),
        sa.Column('fact_code', sa.String(length=64), nullable=False),
        sa.Column('value_type', sa.String(length=10), nullable=False),
        sa.Column('value_decimal', sa.Numeric(), nullable=True),
        sa.Column('value_date', sa.Date(), nullable=True),
        sa.Column('value_text', sa.String(), nullable=True),
        sa.Column('unit', sa.String(length=10), nullable=True),
        sa.Column('reliability', sa.String(length=12), nullable=False),
        sa.Column('reason_code', sa.String(length=40), nullable=True),
        sa.Column('raw_value', sa.JSON(), nullable=True),
        sa.Column('source_path', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("reliability IN ('RELIABLE', 'UNRELIABLE', 'ABSENT')", name='ck_fact_reliability'),
        sa.CheckConstraint(
            "value_type IN ('AMOUNT', 'RATE', 'DATE', 'MONTH', 'TEXT', 'CODE')", name='ck_fact_value_type'
        ),
        sa.CheckConstraint('item_index >= 0', name='ck_fact_index'),
        sa.CheckConstraint("reliability <> 'UNRELIABLE' OR reason_code IS NOT NULL", name='ck_fact_reason'),
        sa.CheckConstraint(
            "(reliability <> 'RELIABLE' AND value_decimal IS NULL AND value_date IS NULL AND value_text IS NULL)"
            " OR (reliability = 'RELIABLE' AND ("
            "(value_type IN ('AMOUNT', 'RATE') AND value_decimal IS NOT NULL AND value_date IS NULL AND value_text IS NULL)"
            " OR (value_type IN ('DATE', 'MONTH') AND value_date IS NOT NULL AND value_decimal IS NULL AND value_text IS NULL)"
            " OR (value_type IN ('TEXT', 'CODE') AND value_text IS NOT NULL AND value_decimal IS NULL AND value_date IS NULL)"
            "))",
            name='ck_fact_value_consistency',
        ),
        sa.ForeignKeyConstraint(['snapshot_id'], ['document_snapshots.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('snapshot_id', 'collection', 'item_index', 'fact_code', name='uq_snapshot_fact_key'),
    )


def downgrade() -> None:
    """Downgrade schema.

    Supprime les snapshots et leurs faits. Ce sont des donnees derivees : les champs valides restent dans
    documents.extracted_data et dans audit_log (PIECE_VALIDEE).
    """
    op.drop_table('snapshot_facts')
    op.drop_index('ix_snapshots_company_period_current', table_name='document_snapshots',
                  postgresql_where=sa.text("status = 'CURRENT'"))
    op.drop_index('uq_snapshot_current_per_document', table_name='document_snapshots',
                  postgresql_where=sa.text("status = 'CURRENT'"))
    op.drop_table('document_snapshots')
    op.drop_constraint('uq_tax_periods_id_company', 'tax_periods', type_='unique')
    op.drop_constraint('uq_documents_id_company', 'documents', type_='unique')