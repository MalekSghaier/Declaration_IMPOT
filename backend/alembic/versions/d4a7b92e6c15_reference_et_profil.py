"""reference_values, profile_values et companies.taxpayer_type

Revision ID: d4a7b92e6c15
Revises: ba2f4d70f618
Create Date: 2026-10-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a7b92e6c15'
down_revision: Union[str, Sequence[str], None] = 'ba2f4d70f618'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('reference_values',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(), nullable=False),
    sa.Column('aliases', sa.JSON(), nullable=True),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('source', sa.String(), nullable=True),
    sa.Column('source_reference', sa.String(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='NEEDS_VERIFICATION', nullable=False),
    sa.Column('extra', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('category', 'code', name='uq_reference_category_code')
    )
    op.create_index(op.f('ix_reference_values_category'), 'reference_values', ['category'], unique=False)

    op.create_table('profile_values',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('attribute_code', sa.String(length=64), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source_document_id', sa.Integer(), nullable=True),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_profile_company_attr', 'profile_values', ['company_id', 'attribute_code'], unique=False)
    op.create_index('uq_profile_value_current', 'profile_values', ['company_id', 'attribute_code'], unique=True, postgresql_where=sa.text('valid_to IS NULL'))

    op.add_column('companies', sa.Column('taxpayer_type', sa.String(length=64), nullable=True))

    # Societes deja verrouillees : leurs valeurs confirmees deviennent les premieres valeurs du profil.
    # (Copie de donnees existantes. 'activite' n'est pas copiee : elle ira dans la table des activites, etape 2c.)
    op.execute(
        """
        INSERT INTO profile_values (company_id, attribute_code, value, status, reason)
        SELECT c.id, v.code, v.val, 'CONFIRMED', 'MIGRATION_INITIALE'
        FROM companies AS c
        CROSS JOIN LATERAL (VALUES
            ('raison_sociale', to_json(c.raison_sociale)),
            ('matricule_fiscal', to_json(c.matricule_fiscal)),
            ('adresse', to_json(c.adresse)),
            ('forme_juridique', to_json(c.forme_juridique)),
            ('code_tva', to_json(c.code_tva)),
            ('code_categorie', to_json(c.code_categorie)),
            ('capital', to_json(c.capital)),
            ('date_creation', to_json(c.date_creation)),
            ('dirigeant', to_json(c.dirigeant))
        ) AS v(code, val)
        WHERE c.status = 'LOCKED' AND v.val IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('companies', 'taxpayer_type')
    op.drop_index('uq_profile_value_current', table_name='profile_values', postgresql_where=sa.text('valid_to IS NULL'))
    op.drop_index('ix_profile_company_attr', table_name='profile_values')
    op.drop_table('profile_values')
    op.drop_index(op.f('ix_reference_values_category'), table_name='reference_values')
    op.drop_table('reference_values')