"""policy clause embeddings

Revision ID: k1f2g3h4i5j6
Revises: j0e1f2g3h4i5
Create Date: 2026-10-27 11:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "k1f2g3h4i5j6"
down_revision = "j0e1f2g3h4i5"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("gov_policy_clause", schema=None) as batch_op:
        batch_op.add_column(sa.Column("embedding", _json_col(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "embedding_model",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("gov_policy_clause", schema=None) as batch_op:
        batch_op.drop_column("embedding_model")
        batch_op.drop_column("embedding")
