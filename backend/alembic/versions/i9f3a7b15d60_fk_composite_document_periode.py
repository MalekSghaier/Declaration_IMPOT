"""FK composite documents (tax_period_id, company_id) -> tax_periods (id, company_id)

Revision ID: i9f3a7b15d60
Revises: h8e2f6a04c59
Create Date: 2026-10-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'i9f3a7b15d60'
down_revision: Union[str, Sequence[str], None] = 'h8e2f6a04c59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Une piece ne peut plus pointer vers la periode fiscale d'une autre societe.

    Controle prealable : si des donnees incoherentes existent, la migration s'arrete SANS rien modifier ni
    supprimer ; elles doivent etre examinees et corrigees manuellement. tax_period_id NULL reste possible
    (une FK composite n'est pas controlee quand une de ses colonnes est NULL).
    La cible (tax_periods.id, tax_periods.company_id) est couverte par uq_tax_periods_id_company.
    """
    bind = op.get_bind()
    incoherent = bind.execute(
        sa.text(
            "SELECT d.id FROM documents d JOIN tax_periods t ON t.id = d.tax_period_id "
            "WHERE t.company_id <> d.company_id ORDER BY d.id"
        )
    ).scalars().all()
    if incoherent:
        raise RuntimeError(
            f"{len(incoherent)} piece(s) pointent vers la periode d'une autre societe "
            f"(documents.id : {incoherent[:20]}). Corriger ces donnees manuellement, puis relancer."
        )
    op.drop_constraint('fk_documents_tax_period_id', 'documents', type_='foreignkey')
    op.create_foreign_key(
        'fk_documents_tax_period', 'documents', 'tax_periods',
        ['tax_period_id', 'company_id'], ['id', 'company_id'],
    )


def downgrade() -> None:
    """Retour a la FK simple vers tax_periods.id (comportement anterieur, donnees inchangees)."""
    op.drop_constraint('fk_documents_tax_period', 'documents', type_='foreignkey')
    op.create_foreign_key(
        'fk_documents_tax_period_id', 'documents', 'tax_periods', ['tax_period_id'], ['id']
    )