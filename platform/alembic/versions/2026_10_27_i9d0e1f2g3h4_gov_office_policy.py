"""gov office policy knowledge

Revision ID: i9d0e1f2g3h4
Revises: h8c9d0e1f2g3
Create Date: 2026-10-27 10:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "i9d0e1f2g3h4"
down_revision = "h8c9d0e1f2g3"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "gov_policy_document",
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("document_number", sa.String(length=128), nullable=False),
        sa.Column("issuing_authority", sa.String(length=256), nullable=False),
        sa.Column("jurisdiction", sa.String(length=128), nullable=False),
        sa.Column("policy_level", sa.String(length=64), nullable=False),
        sa.Column("knowledge_status", sa.String(length=24), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("permission_level", sa.Integer(), nullable=False),
        sa.Column("meta", _json_col(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("doc_id"),
    )
    op.create_index("ix_gov_policy_document_document_number", "gov_policy_document", ["document_number"])
    op.create_index("ix_gov_policy_document_jurisdiction", "gov_policy_document", ["jurisdiction"])
    op.create_index(
        "ix_gov_policy_document_knowledge_status", "gov_policy_document", ["knowledge_status"]
    )
    op.create_index("ix_gov_policy_document_source_type", "gov_policy_document", ["source_type"])

    op.create_table(
        "gov_policy_version",
        sa.Column("version_id", sa.String(length=192), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("version_label", sa.String(length=96), nullable=False),
        sa.Column("document_number", sa.String(length=128), nullable=False),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column(
            "source_retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["doc_id"], ["gov_policy_document.doc_id"]),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint("doc_id", "version_label", name="uq_gov_policy_version_label"),
    )
    op.create_index("ix_gov_policy_version_doc_id", "gov_policy_version", ["doc_id"])
    op.create_index("ix_gov_policy_version_review_status", "gov_policy_version", ["review_status"])
    op.create_index(
        "ix_gov_policy_version_document_number", "gov_policy_version", ["document_number"]
    )
    op.create_index("ix_gov_policy_version_effective_from", "gov_policy_version", ["effective_from"])
    op.create_index("ix_gov_policy_version_effective_to", "gov_policy_version", ["effective_to"])

    op.create_table(
        "gov_policy_clause",
        sa.Column("clause_id", sa.String(length=256), nullable=False),
        sa.Column("version_id", sa.String(length=192), nullable=False),
        sa.Column("clause_no", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("conditions", _json_col(), nullable=False),
        sa.Column("exceptions", _json_col(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("meta", _json_col(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["version_id"], ["gov_policy_version.version_id"]),
        sa.PrimaryKeyConstraint("clause_id"),
        sa.UniqueConstraint("version_id", "clause_no", name="uq_gov_policy_clause_no"),
    )
    op.create_index("ix_gov_policy_clause_version_id", "gov_policy_clause", ["version_id"])

    op.create_table(
        "gov_policy_relation",
        sa.Column("relation_id", sa.String(length=256), nullable=False),
        sa.Column("from_version_id", sa.String(length=192), nullable=False),
        sa.Column("to_version_id", sa.String(length=192), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("relation_id"),
    )
    op.create_index("ix_gov_policy_relation_from_version_id", "gov_policy_relation", ["from_version_id"])
    op.create_index("ix_gov_policy_relation_to_version_id", "gov_policy_relation", ["to_version_id"])
    op.create_index("ix_gov_policy_relation_relation_type", "gov_policy_relation", ["relation_type"])
    op.create_index("ix_gov_policy_relation_review_status", "gov_policy_relation", ["review_status"])

    op.create_table(
        "gov_policy_import_operation",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=192), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_gov_policy_import_operation_idempotency_key",
        "gov_policy_import_operation",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_gov_policy_import_operation_doc_id", "gov_policy_import_operation", ["doc_id"])


def downgrade() -> None:
    op.drop_index("ix_gov_policy_import_operation_doc_id", table_name="gov_policy_import_operation")
    op.drop_index(
        "ix_gov_policy_import_operation_idempotency_key",
        table_name="gov_policy_import_operation",
    )
    op.drop_table("gov_policy_import_operation")
    op.drop_index("ix_gov_policy_relation_review_status", table_name="gov_policy_relation")
    op.drop_index("ix_gov_policy_relation_relation_type", table_name="gov_policy_relation")
    op.drop_index("ix_gov_policy_relation_to_version_id", table_name="gov_policy_relation")
    op.drop_index("ix_gov_policy_relation_from_version_id", table_name="gov_policy_relation")
    op.drop_table("gov_policy_relation")
    op.drop_index("ix_gov_policy_clause_version_id", table_name="gov_policy_clause")
    op.drop_table("gov_policy_clause")
    op.drop_index("ix_gov_policy_version_effective_to", table_name="gov_policy_version")
    op.drop_index("ix_gov_policy_version_effective_from", table_name="gov_policy_version")
    op.drop_index("ix_gov_policy_version_document_number", table_name="gov_policy_version")
    op.drop_index("ix_gov_policy_version_review_status", table_name="gov_policy_version")
    op.drop_index("ix_gov_policy_version_doc_id", table_name="gov_policy_version")
    op.drop_table("gov_policy_version")
    op.drop_index("ix_gov_policy_document_source_type", table_name="gov_policy_document")
    op.drop_index("ix_gov_policy_document_knowledge_status", table_name="gov_policy_document")
    op.drop_index("ix_gov_policy_document_jurisdiction", table_name="gov_policy_document")
    op.drop_index("ix_gov_policy_document_document_number", table_name="gov_policy_document")
    op.drop_table("gov_policy_document")
