"""gov document drafts

Revision ID: l2g3h4i5j6k7
Revises: k1f2g3h4i5j6
Create Date: 2026-10-27 11:40:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "l2g3h4i5j6k7"
down_revision = "k1f2g3h4i5j6"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "gov_document_draft",
        sa.Column("draft_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("issuing_authority", sa.String(length=256), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("sections", _json_col(), nullable=False),
        sa.Column("facts", _json_col(), nullable=False),
        sa.Column("policy_citations", _json_col(), nullable=False),
        sa.Column("checks", _json_col(), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=32), nullable=False),
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
    op.create_index("ix_gov_document_draft_owner_id", "gov_document_draft", ["owner_id"])
    op.create_index("ix_gov_document_draft_doc_type", "gov_document_draft", ["doc_type"])
    op.create_index("ix_gov_document_draft_status", "gov_document_draft", ["status"])
    op.create_index(
        "ix_gov_document_draft_idempotency_key",
        "gov_document_draft",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gov_document_draft_idempotency_key", table_name="gov_document_draft"
    )
    op.drop_index("ix_gov_document_draft_status", table_name="gov_document_draft")
    op.drop_index("ix_gov_document_draft_doc_type", table_name="gov_document_draft")
    op.drop_index("ix_gov_document_draft_owner_id", table_name="gov_document_draft")
    op.drop_table("gov_document_draft")
