"""run dispatch outbox

Revision ID: h8c9d0e1f2g3
Revises: g7b8c9d0e1f2
Create Date: 2026-10-23 22:00:00.000000+00:00

05-B：审批消费/effect dispatched 同事务写入的恢复派发 outbox。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h8c9d0e1f2g3"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_run_outbox",
        sa.Column("outbox_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("effect_key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("payload_ref", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("outbox_id"),
        sa.UniqueConstraint("effect_key"),
    )
    with op.batch_alter_table("agent_run_outbox", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_run_outbox_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_run_outbox_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agent_run_outbox", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_run_outbox_status"))
        batch_op.drop_index(batch_op.f("ix_agent_run_outbox_run_id"))
    op.drop_table("agent_run_outbox")
