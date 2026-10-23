"""execution approvals and run effects

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-23 18:30:00.000000+00:00

06-A2 一次性审批 + 05-B 副作用账本。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "execution_approval",
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("effect_key", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=False),
        sa.Column("args_digest", sa.String(length=64), nullable=False),
        sa.Column("permission_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("preview", _json_col(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint("effect_key", name="uq_execution_approval_effect"),
    )
    with op.batch_alter_table("execution_approval", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_execution_approval_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_execution_approval_domain_id"), ["domain_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_execution_approval_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_execution_approval_status"), ["status"], unique=False)

    op.create_table(
        "agent_run_effect",
        sa.Column("effect_key", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("node", sa.String(length=48), nullable=False),
        sa.Column("invocation_index", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("external_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("result_ref", sa.String(length=128), nullable=True),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("effect_key"),
    )
    with op.batch_alter_table("agent_run_effect", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_run_effect_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_run_effect_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agent_run_effect", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_run_effect_status"))
        batch_op.drop_index(batch_op.f("ix_agent_run_effect_run_id"))
    op.drop_table("agent_run_effect")
    with op.batch_alter_table("execution_approval", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_execution_approval_status"))
        batch_op.drop_index(batch_op.f("ix_execution_approval_run_id"))
        batch_op.drop_index(batch_op.f("ix_execution_approval_domain_id"))
        batch_op.drop_index(batch_op.f("ix_execution_approval_owner_id"))
    op.drop_table("execution_approval")
