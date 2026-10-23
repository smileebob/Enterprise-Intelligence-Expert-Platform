"""chat session summaries

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-23 21:00:00.000000+00:00

04 §4：确定性会话摘要 + 版本 CAS；下一轮消费摘要与未覆盖最近消息。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "chat_session_summary",
        sa.Column("summary_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("covered_message_ids", _json_col(), nullable=False),
        sa.Column("structured_slots", _json_col(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("summary_id"),
        sa.UniqueConstraint("session_id"),
    )
    with op.batch_alter_table("chat_session_summary", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_chat_session_summary_session_id"), ["session_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_chat_session_summary_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_chat_session_summary_domain_id"), ["domain_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("chat_session_summary", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chat_session_summary_domain_id"))
        batch_op.drop_index(batch_op.f("ix_chat_session_summary_owner_id"))
        batch_op.drop_index(batch_op.f("ix_chat_session_summary_session_id"))
    op.drop_table("chat_session_summary")
