"""运行/事件/载荷/检查点 ORM（05 §4）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class AgentRun(Base):
    """可变控制投影：last_seq/status 与对应事件同事务提交。"""

    __tablename__ = "agent_run"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="live")  # live/replay/fork
    parent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fork_checkpoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    status_version: Mapped[int] = mapped_column(default=0)
    last_seq: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest: Mapped[dict] = mapped_column(json_col(), default=dict)
    privacy_state: Mapped[str] = mapped_column(String(16), default="available")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRunEvent(Base):
    """追加事实：envelope 只存最少非敏感信息，明细在受控载荷。"""

    __tablename__ = "agent_run_event"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),)

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1")
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    node: Mapped[str] = mapped_column(String(48), default="")
    node_attempt: Mapped[int] = mapped_column(default=1)
    call_id: Mapped[str] = mapped_column(String(128), default="")
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(json_col(), default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    event_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class RunPayload(Base):
    """受控载荷元数据；物理对象在加密存储，key_ref 删除即 crypto-shredding。"""

    __tablename__ = "run_payload"

    opaque_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    classification: Mapped[str] = mapped_column(String(24), default="internal")
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(256))
    cipher_digest: Mapped[str] = mapped_column(String(64))
    content_digest: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(default=0)
    key_ref: Mapped[str] = mapped_column(Text, default="")
    schema_version: Mapped[str] = mapped_column(String(16), default="1")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_state: Mapped[str] = mapped_column(String(16), default="available", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class RunPayloadSource(Base):
    """载荷来源与派生关系（消息/知识块/记忆等）。"""

    __tablename__ = "run_payload_source"
    __table_args__ = (
        UniqueConstraint("opaque_id", "source_type", "source_ref", name="uq_run_payload_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    opaque_id: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class ExecutionApproval(Base):
    """危险工具一次性审批：申请时绑定 effect/工具/参数/权限版本，最多一份有效批准。"""

    __tablename__ = "execution_approval"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="")
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    effect_key: Mapped[str] = mapped_column(String(128), unique=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    tool_version: Mapped[str] = mapped_column(String(32), default="1")
    args_digest: Mapped[str] = mapped_column(String(64))
    permission_version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    preview: Mapped[dict] = mapped_column(json_col(), default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class AgentRunEffect(Base):
    """本地/外部副作用账本：effect_key 唯一，状态由业务事务同提交推进。"""

    __tablename__ = "agent_run_effect"

    effect_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    node: Mapped[str] = mapped_column(String(48), default="")
    invocation_index: Mapped[int] = mapped_column(default=0)
    operation: Mapped[str] = mapped_column(String(64), default="")
    request_digest: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(24), default="prepared", index=True)
    external_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class AgentRunOutbox(Base):
    """派发 outbox（05 §6.1）：与审批消费/effect 同事务提交，供恢复派发。"""

    __tablename__ = "agent_run_outbox"

    outbox_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    effect_key: Mapped[str] = mapped_column(String(128), unique=True)
    operation: Mapped[str] = mapped_column(String(64), default="")
    payload_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutputReview(Base):
    """输出审核（06-C）：绑定草稿摘要与策略版本，批准后改稿需重审。"""

    __tablename__ = "output_review"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="")
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    draft_digest: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(32), default="output-policy-v1")
    action_requested: Mapped[str] = mapped_column(String(16), default="review")
    reasons: Mapped[list] = mapped_column(json_col(), default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reviewer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class AgentRunCheckpoint(Base):
    """节点完成/合法等待点快照（05-B 使用；先建表保持迁移稳定）。"""

    __tablename__ = "agent_run_checkpoint"

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    after_seq: Mapped[int] = mapped_column(default=0)
    next_node: Mapped[str] = mapped_column(String(48), default="")
    state_schema_version: Mapped[str] = mapped_column(String(16), default="1")
    state_ref: Mapped[str] = mapped_column(String(64), default="")
    manifest_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint_kind: Mapped[str] = mapped_column(String(24), default="node")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
