"""runs, append-only events, controlled payloads

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-23 17:30:00.000000+00:00

05-A：运行控制投影、追加事件（唯一 run_id+seq）、受控载荷与来源、检查点表。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "agent_run",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("parent_run_id", sa.String(length=64), nullable=True),
        sa.Column("fork_checkpoint_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("status_version", sa.Integer(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest", _json_col(), nullable=False),
        sa.Column("privacy_state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    with op.batch_alter_table("agent_run", schema=None) as batch_op:
        for name, column in (
            ("ix_agent_run_session_id", "session_id"),
            ("ix_agent_run_owner_id", "owner_id"),
            ("ix_agent_run_domain_id", "domain_id"),
            ("ix_agent_run_status", "status"),
        ):
            batch_op.create_index(batch_op.f(name), [column], unique=False)

    op.create_table(
        "agent_run_event",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("node", sa.String(length=48), nullable=False),
        sa.Column("node_attempt", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("causation_id", sa.String(length=64), nullable=True),
        sa.Column("payload_ref", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", _json_col(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
    )
    with op.batch_alter_table("agent_run_event", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_run_event_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_run_event_seq"), ["seq"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_run_event_event_type"), ["event_type"], unique=False)

    op.create_table(
        "run_payload",
        sa.Column("opaque_id", sa.String(length=64), nullable=False),
        sa.Column("classification", sa.String(length=24), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=256), nullable=False),
        sa.Column("cipher_digest", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("key_ref", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("opaque_id"),
    )
    with op.batch_alter_table("run_payload", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_run_payload_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_run_payload_domain_id"), ["domain_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_run_payload_deletion_state"), ["deletion_state"], unique=False)

    op.create_table(
        "run_payload_source",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("opaque_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("opaque_id", "source_type", "source_ref", name="uq_run_payload_source"),
    )
    with op.batch_alter_table("run_payload_source", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_run_payload_source_opaque_id"), ["opaque_id"], unique=False)

    op.create_table(
        "agent_run_checkpoint",
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("after_seq", sa.Integer(), nullable=False),
        sa.Column("next_node", sa.String(length=48), nullable=False),
        sa.Column("state_schema_version", sa.String(length=16), nullable=False),
        sa.Column("state_ref", sa.String(length=64), nullable=False),
        sa.Column("manifest_ref", sa.String(length=64), nullable=True),
        sa.Column("checkpoint_kind", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("checkpoint_id"),
    )
    with op.batch_alter_table("agent_run_checkpoint", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_run_checkpoint_run_id"), ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("agent_run_checkpoint", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_run_checkpoint_run_id"))
    op.drop_table("agent_run_checkpoint")
    with op.batch_alter_table("run_payload_source", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_run_payload_source_opaque_id"))
    op.drop_table("run_payload_source")
    with op.batch_alter_table("run_payload", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_run_payload_deletion_state"))
        batch_op.drop_index(batch_op.f("ix_run_payload_domain_id"))
        batch_op.drop_index(batch_op.f("ix_run_payload_owner_id"))
    op.drop_table("run_payload")
    with op.batch_alter_table("agent_run_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_run_event_event_type"))
        batch_op.drop_index(batch_op.f("ix_agent_run_event_seq"))
        batch_op.drop_index(batch_op.f("ix_agent_run_event_run_id"))
    op.drop_table("agent_run_event")
    with op.batch_alter_table("agent_run", schema=None) as batch_op:
        for name in (
            "ix_agent_run_status",
            "ix_agent_run_domain_id",
            "ix_agent_run_owner_id",
            "ix_agent_run_session_id",
        ):
            batch_op.drop_index(batch_op.f(name))
    op.drop_table("agent_run")
