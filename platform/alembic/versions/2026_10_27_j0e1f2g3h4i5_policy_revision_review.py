"""policy revision and relation review

Revision ID: j0e1f2g3h4i5
Revises: i9d0e1f2g3h4
Create Date: 2026-10-27 10:40:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "j0e1f2g3h4i5"
down_revision = "i9d0e1f2g3h4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gov_policy_version", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("gov_policy_relation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reviewed_by", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("gov_policy_relation", schema=None) as batch_op:
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
    with op.batch_alter_table("gov_policy_version", schema=None) as batch_op:
        batch_op.drop_column("revision")
