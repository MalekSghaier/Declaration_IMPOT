"""extracted_fields et tracabilite

Revision ID: b547ff89984f
Revises: e5b8c3d71f26
Create Date: 2026-10-01 16:42:57.171805

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b547ff89984f'
down_revision: Union[str, Sequence[str], None] = 'e5b8c3d71f26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- Nouvelle table extracted_fields ---
    op.create_table(
        'extracted_fields',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('field_code', sa.String(length=64), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('extraction_method', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            'confidence IS NULL OR (confidence >= 0 AND confidence <= 1)',
            name='ck_extracted_field_confidence_range',
        ),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'field_code', name='uq_extracted_field_document_code'),
    )
    op.create_index('ix_extracted_fields_document', 'extracted_fields', ['document_id'], unique=False)

    # --- activities : tracabilite par champ ---
    op.add_column('activities', sa.Column('page', sa.Integer(), nullable=True))
    op.add_column('activities', sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True))
    op.create_check_constraint(
        'ck_activity_confidence_range',
        'activities',
        'confidence IS NULL OR (confidence >= 0 AND confidence <= 1)',
    )

    # --- establishments : document source, page, confidence ---
    op.add_column('establishments', sa.Column('source_document_id', sa.Integer(), nullable=True))
    op.add_column('establishments', sa.Column('page', sa.Integer(), nullable=True))
    op.add_column('establishments', sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True))
    op.create_foreign_key(
        'fk_establishments_source_document_id',
        'establishments', 'documents',
        ['source_document_id'], ['id'],
    )
    op.create_check_constraint(
        'ck_establishment_confidence_range',
        'establishments',
        'confidence IS NULL OR (confidence >= 0 AND confidence <= 1)',
    )

    # --- profile_values : lien vers la ligne extracted_fields source ---
    op.add_column('profile_values', sa.Column('extracted_field_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_profile_values_extracted_field_id',
        'profile_values', 'extracted_fields',
        ['extracted_field_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_profile_values_extracted_field_id', 'profile_values', type_='foreignkey')
    op.drop_column('profile_values', 'extracted_field_id')

    op.drop_constraint('ck_establishment_confidence_range', 'establishments', type_='check')
    op.drop_constraint('fk_establishments_source_document_id', 'establishments', type_='foreignkey')
    op.drop_column('establishments', 'confidence')
    op.drop_column('establishments', 'page')
    op.drop_column('establishments', 'source_document_id')

    op.drop_constraint('ck_activity_confidence_range', 'activities', type_='check')
    op.drop_column('activities', 'confidence')
    op.drop_column('activities', 'page')

    op.drop_index('ix_extracted_fields_document', table_name='extracted_fields')
    op.drop_table('extracted_fields')