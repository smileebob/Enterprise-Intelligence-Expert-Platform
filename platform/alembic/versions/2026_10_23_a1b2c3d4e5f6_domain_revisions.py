"""domain revisions and idempotent operations

Revision ID: a1b2c3d4e5f6
Revises: ddbd7cabd7ed
Create Date: 2026-10-23 16:00:00.000000+00:00

领域配置 revision/CAS（02）：dom_instance 增加 config_rev 与 active/draft 指针；
新增不可变 dom_revision 与幂等记录 dom_operation；旧实例回填初始 revision。
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "ddbd7cabd7ed"
branch_labels = None
depends_on = None


def _json_col() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _hash(config: dict) -> str:
    raw = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    with op.batch_alter_table("dom_instance", schema=None) as batch_op:
        batch_op.add_column(sa.Column("config_rev", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("active_revision_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("draft_revision_id", sa.String(length=64), nullable=True))

    op.create_table(
        "dom_revision",
        sa.Column("revision_id", sa.String(length=64), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("config_rev", sa.Integer(), nullable=False),
        sa.Column("config", _json_col(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("change_kind", sa.String(length=32), nullable=False),
        sa.Column("restored_from", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint("domain_id", "config_rev", name="uq_dom_revision_domain_rev"),
    )
    with op.batch_alter_table("dom_revision", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dom_revision_domain_id"), ["domain_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_dom_revision_config_rev"), ["config_rev"], unique=False)

    op.create_table(
        "dom_operation",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("domain_id", sa.String(length=64), nullable=False),
        sa.Column("idem_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response", _json_col(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint("actor_id", "operation", "domain_id", "idem_key", name="uq_dom_operation_key"),
    )
    with op.batch_alter_table("dom_operation", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dom_operation_domain_id"), ["domain_id"], unique=False)

    # 旧库回填：每个实例生成初始 revision；仅在线实例设置 active 指针
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT domain_id, config, status, created_by, version FROM dom_instance")
    ).mappings().all()
    for row in rows:
        config = row["config"]
        if isinstance(config, str):  # SQLite 文本 JSON 兜底
            config = json.loads(config or "{}")
        if not isinstance(config, dict) or not config:
            continue
        rev_id = f"REV-{row['domain_id']}-init"
        status = row["status"]
        bind.execute(
            sa.text(
                "INSERT INTO dom_revision "
                "(revision_id, domain_id, config_rev, config, code_version, schema_version, content_hash, change_kind, restored_from, created_by) "
                "VALUES (:revision_id, :domain_id, 1, :config, '', '1', :content_hash, :change_kind, NULL, :created_by)"
            ),
            {
                "revision_id": rev_id,
                "domain_id": row["domain_id"],
                "config": json.dumps(config, ensure_ascii=False),
                "content_hash": _hash(config),
                "change_kind": "publish" if status == "online" else "draft",
                "created_by": row["created_by"],
            },
        )
        bind.execute(
            sa.text(
                "UPDATE dom_instance SET config_rev = 1, active_revision_id = :active, draft_revision_id = :draft "
                "WHERE domain_id = :domain_id"
            ),
            {
                "active": rev_id if status == "online" else None,
                "draft": None if status == "online" else rev_id,
                "domain_id": row["domain_id"],
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("dom_operation", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dom_operation_domain_id"))
    op.drop_table("dom_operation")
    with op.batch_alter_table("dom_revision", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dom_revision_config_rev"))
        batch_op.drop_index(batch_op.f("ix_dom_revision_domain_id"))
    op.drop_table("dom_revision")
    with op.batch_alter_table("dom_instance", schema=None) as batch_op:
        batch_op.drop_column("draft_revision_id")
        batch_op.drop_column("active_revision_id")
        batch_op.drop_column("config_rev")
