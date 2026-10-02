"""activities et establishments, avec reprise de companies.activite et companies.adresse

Revision ID: e5b8c3d71f26
Revises: d4a7b92e6c15
Create Date: 2026-10-01 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b8c3d71f26'
down_revision: Union[str, Sequence[str], None] = 'd4a7b92e6c15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (additif : companies n'est pas modifiee)."""
    op.create_table('activities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=True),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source_document_id', sa.Integer(), nullable=True),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_activities_company', 'activities', ['company_id'], unique=False)
    op.create_index('uq_activity_primary_current', 'activities', ['company_id'], unique=True, postgresql_where=sa.text('is_primary AND valid_to IS NULL'))

    op.create_table('establishments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('number', sa.String(length=10), nullable=True),
    sa.Column('number_status', sa.String(length=20), nullable=False),
    sa.Column('is_head_office', sa.Boolean(), nullable=False),
    sa.Column('address', sa.String(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_establishments_company', 'establishments', ['company_id'], unique=False)
    op.create_index('uq_establishment_head_current', 'establishments', ['company_id'], unique=True, postgresql_where=sa.text('is_head_office AND valid_to IS NULL'))
    op.create_index('uq_establishment_number_current', 'establishments', ['company_id', 'number'], unique=True, postgresql_where=sa.text('number IS NOT NULL AND valid_to IS NULL'))

    # Reprise (copie) des societes deja verrouillees. Les colonnes de companies restent intactes.
    # Activite : devient l'activite principale ; le code reste NULL (nomenclature officielle non confirmee).
    op.execute(
        """
        INSERT INTO activities (company_id, label, code, is_primary, status, reason)
        SELECT id, btrim(activite), NULL, true, 'CONFIRMED', 'MIGRATION_INITIALE'
        FROM companies
        WHERE status = 'LOCKED' AND activite IS NOT NULL AND btrim(activite) <> ''
        """
    )
    # Adresse : devient le siege. Le numero '000' est une valeur de reprise, a verifier (NEEDS_VERIFICATION).
    op.execute(
        """
        INSERT INTO establishments (company_id, number, number_status, is_head_office, address, status, reason)
        SELECT id, '000', 'NEEDS_VERIFICATION', true, btrim(adresse), 'CONFIRMED', 'MIGRATION_INITIALE'
        FROM companies
        WHERE status = 'LOCKED' AND adresse IS NOT NULL AND btrim(adresse) <> ''
        """
    )


def downgrade() -> None:
    """Downgrade schema (companies contient toujours les donnees d'origine)."""
    op.drop_index('uq_establishment_number_current', table_name='establishments', postgresql_where=sa.text('number IS NOT NULL AND valid_to IS NULL'))
    op.drop_index('uq_establishment_head_current', table_name='establishments', postgresql_where=sa.text('is_head_office AND valid_to IS NULL'))
    op.drop_index('ix_establishments_company', table_name='establishments')
    op.drop_table('establishments')
    op.drop_index('uq_activity_primary_current', table_name='activities', postgresql_where=sa.text('is_primary AND valid_to IS NULL'))
    op.drop_index('ix_activities_company', table_name='activities')
    op.drop_table('activities')