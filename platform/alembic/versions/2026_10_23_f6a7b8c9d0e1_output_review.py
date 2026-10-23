"""output reviews

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-23 20:00:00.000000+00:00

06-C 输出审核持久化（绑定草稿摘要与策略版本）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "output_review",
        sa.Column("review_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("draft_digest", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("action_requested", sa.String(length=16), nullable=False),
        sa.Column("reasons", _json_col(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("review_id"),
    )
    with op.batch_alter_table("output_review", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_output_review_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_output_review_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_output_review_domain_id"), ["domain_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_output_review_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("output_review", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_output_review_status"))
        batch_op.drop_index(batch_op.f("ix_output_review_domain_id"))
        batch_op.drop_index(batch_op.f("ix_output_review_owner_id"))
        batch_op.drop_index(batch_op.f("ix_output_review_run_id"))
    op.drop_table("output_review")
