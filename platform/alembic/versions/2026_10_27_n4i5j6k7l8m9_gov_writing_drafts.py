"""gov writing drafts

Revision ID: n4i5j6k7l8m9
Revises: m3h4i5j6k7l8
Create Date: 2026-10-27 13:10:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "n4i5j6k7l8m9"
down_revision = "m3h4i5j6k7l8"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "gov_writing_draft",
        sa.Column("draft_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(length=256), nullable=False),
        sa.Column("jurisdiction", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("materials", _json_col(), nullable=False),
        sa.Column("outline", _json_col(), nullable=False),
        sa.Column("policy_citations", _json_col(), nullable=False),
        sa.Column("result", _json_col(), nullable=False),
        sa.Column("checks", _json_col(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("draft_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_gov_writing_draft_owner_id", "gov_writing_draft", ["owner_id"])
    op.create_index("ix_gov_writing_draft_mode", "gov_writing_draft", ["mode"])
    op.create_index("ix_gov_writing_draft_status", "gov_writing_draft", ["status"])
    op.create_index(
        "ix_gov_writing_draft_idempotency_key",
        "gov_writing_draft",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gov_writing_draft_idempotency_key", table_name="gov_writing_draft"
    )
    op.drop_index("ix_gov_writing_draft_status", table_name="gov_writing_draft")
    op.drop_index("ix_gov_writing_draft_mode", table_name="gov_writing_draft")
    op.drop_index("ix_gov_writing_draft_owner_id", table_name="gov_writing_draft")
    op.drop_table("gov_writing_draft")
