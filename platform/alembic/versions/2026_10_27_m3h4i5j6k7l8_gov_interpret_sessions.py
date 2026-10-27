"""gov interpret sessions

Revision ID: m3h4i5j6k7l8
Revises: l2g3h4i5j6k7
Create Date: 2026-10-27 12:20:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m3h4i5j6k7l8"
down_revision = "l2g3h4i5j6k7"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "gov_interpret_session",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("turns", _json_col(), nullable=False),
        sa.Column("last_result", _json_col(), nullable=True),
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
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_gov_interpret_session_owner_id", "gov_interpret_session", ["owner_id"])
    op.create_index("ix_gov_interpret_session_status", "gov_interpret_session", ["status"])

    op.create_table(
        "gov_interpret_operation",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", _json_col(), nullable=False),
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
        "ix_gov_interpret_operation_session_id", "gov_interpret_operation", ["session_id"]
    )
    op.create_index(
        "ix_gov_interpret_operation_idempotency_key",
        "gov_interpret_operation",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gov_interpret_operation_idempotency_key",
        table_name="gov_interpret_operation",
    )
    op.drop_index(
        "ix_gov_interpret_operation_session_id", table_name="gov_interpret_operation"
    )
    op.drop_table("gov_interpret_operation")
    op.drop_index("ix_gov_interpret_session_status", table_name="gov_interpret_session")
    op.drop_index("ix_gov_interpret_session_owner_id", table_name="gov_interpret_session")
    op.drop_table("gov_interpret_session")
