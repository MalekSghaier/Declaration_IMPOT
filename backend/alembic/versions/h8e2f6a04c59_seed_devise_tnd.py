"""seed de la devise TND dans reference_values (category CURRENCY)

Revision ID: h8e2f6a04c59
Revises: g7d1e5f93b48
Create Date: 2026-10-09 10:10:26.496115

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h8e2f6a04c59'
down_revision: Union[str, Sequence[str], None] = 'g7d1e5f93b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

reference_values = sa.table(
    "reference_values",
    sa.column("category", sa.String),
    sa.column("code", sa.String),
    sa.column("label", sa.String),
    sa.column("aliases", sa.JSON),
    sa.column("source", sa.String),
    sa.column("status", sa.String),
)


def upgrade() -> None:
    """Donnee de reference : devise TND. Idempotent (rien n'est insere si la ligne existe deja).
    Statut NEEDS_VERIFICATION : a passer en CONFIRMED une fois la source verifiee."""
    bind = op.get_bind()
    exists = bind.execute(
        sa.select(sa.func.count())
        .select_from(reference_values)
        .where(reference_values.c.category == "CURRENCY", reference_values.c.code == "TND")
    ).scalar()
    if not exists:
        op.bulk_insert(
            reference_values,
            [
                {
                    "category": "CURRENCY",
                    "code": "TND",
                    "label": "Dinar tunisien",
                    "aliases": ["DT", "DINAR", "DINARS"],
                    "source": "ISO 4217",
                    "status": "NEEDS_VERIFICATION",
                }
            ],
        )


def downgrade() -> None:
    op.execute(
        reference_values.delete().where(
            reference_values.c.category == "CURRENCY", reference_values.c.code == "TND"
        )
    )
