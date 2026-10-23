"""structured growth correction rules

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-10-23 19:10:00.000000+00:00

06-B：原始反馈规则先 pending；管理员显式转为结构化类型后 activate。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("growth_correction_rule", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rule_type", sa.String(length=32), nullable=False, server_default="text_only"))
        batch_op.add_column(sa.Column("target_fact", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("scope", sa.String(length=64), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("approved_by", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_growth_correction_rule_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("growth_correction_rule", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_growth_correction_rule_status"))
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by")
        batch_op.drop_column("scope")
        batch_op.drop_column("target_fact")
        batch_op.drop_column("rule_type")
