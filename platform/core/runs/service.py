"""运行事件服务（05-A）：追加式事件 + CAS seq + 哈希链 + 受控载荷 + inspect。"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from adapters.payload_store import EncryptedPayloadStore, PayloadUnavailable
from app.config import get_settings
from core.gateway.permissions import role_permissions
from core.runs.models import AgentRun, AgentRunCheckpoint, AgentRunEvent, RunPayload

logger = logging.getLogger(__name__)


class RunConflict(Exception):
    """并发追加或状态 CAS 冲突。"""


class RunAccessDenied(Exception):
    """无权查看该运行。"""


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_event(prev_hash: str, envelope: dict[str, Any]) -> str:
    return hashlib.sha256(f"{prev_hash}\n{canonical_json(envelope)}".encode()).hexdigest()  # noqa: S324


@dataclass
class RunManifest:
    code_version: str = ""
    plugin_revision: int = 0
    config_rev: int = 0
    model_mode: str = ""
    model_alias: str = ""
    event_schema_version: str = "1"
    state_schema_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_version": self.code_version,
            "plugin_revision": self.plugin_revision,
            "config_rev": self.config_rev,
            "model_mode": self.model_mode,
            "model_alias": self.model_alias,
            "event_schema_version": self.event_schema_version,
            "state_schema_version": self.state_schema_version,
        }


class RunService:
    def __init__(self, db: Session, payload_store: EncryptedPayloadStore | None = None):
        settings = get_settings()
        self.db = db
        self.settings = settings
        self.store = payload_store or EncryptedPayloadStore(
            db, Path(settings.DATA_DIR) / "run_payloads", settings.SECRET_KEY
        )

    # ---------------- 运行 ----------------
    def create_run(
        self,
        *,
        session_id: str,
        owner_id: str,
        domain_id: str,
        mode: str = "live",
        manifest: RunManifest | dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            run_id=run_id or f"RUN-{uuid.uuid4().hex[:20]}",
            session_id=session_id,
            owner_id=owner_id,
            domain_id=domain_id,
            mode=mode,
            status="running",
            manifest=manifest.to_dict() if isinstance(manifest, RunManifest) else dict(manifest or {}),
            privacy_state="available",
        )
        self.db.add(run)
        self.db.commit()
        self.append_event(run.run_id, "run.started", metadata={"mode": mode, "domain_id": domain_id})
        return run

    def latest_run_for_session(self, session_id: str, *, status: str | None = None) -> AgentRun | None:
        stmt = select(AgentRun).where(AgentRun.session_id == session_id)
        if status:
            stmt = stmt.where(AgentRun.status == status)
        return self.db.execute(stmt.order_by(AgentRun.created_at.desc())).scalars().first()

    def get_run(self, run_id: str) -> AgentRun:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise KeyError(f"运行不存在: {run_id}")
        return run

    def set_status(self, run_id: str, status: str, *, event_type: str | None = None) -> AgentRun:
        """CAS 更新状态投影，并与状态事件同事务提交。"""
        for attempt in range(5):
            try:
                run = self.get_run(run_id)
                expected_version = run.status_version
                self.append_event(
                    run_id,
                    event_type or f"run.{status}",
                    metadata={"status": status},
                    commit=False,
                )
                values: dict[str, Any] = {"status": status, "status_version": expected_version + 1}
                if status in ("completed", "failed", "cancelled"):
                    from core.clock import utc_now

                    values["finished_at"] = utc_now()
                result = self.db.execute(
                    update(AgentRun)
                    .where(AgentRun.run_id == run_id, AgentRun.status_version == expected_version)
                    .values(**values)
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise RunConflict("运行状态已被其他操作更新")
                self.db.commit()
                self.db.refresh(run)
                return run
            except RunConflict:
                self.db.rollback()
                time.sleep(0.01 * (attempt + 1))
        raise RunConflict("运行状态更新冲突重试耗尽")

    def finish_run(self, run_id: str, status: str = "completed") -> AgentRun:
        return self.set_status(run_id, status)

    # ---------------- 事件追加 ----------------
    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        node: str = "",
        metadata: dict[str, Any] | None = None,
        payload: Any | None = None,
        classification: str = "internal",
        sources: list[tuple[str, str]] | None = None,
        event_id: str | None = None,
        call_id: str = "",
        causation_id: str | None = None,
        node_attempt: int = 1,
        commit: bool = True,
    ) -> AgentRunEvent:
        """追加事件：event_id 幂等；seq 以 CAS 分配；载荷先落盘后提交引用。"""
        event_id = event_id or f"EV-{uuid.uuid4().hex[:20]}"
        existing = self.db.get(AgentRunEvent, event_id)
        if existing is not None:
            if existing.run_id != run_id:
                raise ValueError("event_id 已属于其他运行")
            return existing
        payload_bytes = canonical_json(payload).encode("utf-8") if payload is not None else None
        last_error: Exception | None = None
        for attempt in range(6):
            if attempt:
                existing = self.db.get(AgentRunEvent, event_id)
                if existing is not None and existing.run_id == run_id:
                    return existing
            storage_key = ""
            try:
                run = self.db.get(AgentRun, run_id)
                if run is None:
                    raise KeyError(f"运行不存在: {run_id}")
                seq = run.last_seq + 1
                last = self.db.execute(
                    select(AgentRunEvent).where(
                        AgentRunEvent.run_id == run_id, AgentRunEvent.seq == run.last_seq
                    )
                ).scalars().first()
                prev_hash = last.event_hash if last is not None else ""
                meta = dict(metadata or {})
                payload_row = None
                if payload_bytes is not None:
                    payload_row = self.store.put(
                        payload_bytes,
                        owner_id=run.owner_id,
                        domain_id=run.domain_id,
                        classification=classification,
                        sources=sources,
                        schema_version=self.settings.RUN_EVENT_SCHEMA_VERSION,
                        retention_days=self.settings.RUN_PAYLOAD_RETENTION_DAYS,
                    )
                    storage_key = payload_row.storage_key
                envelope = {
                    "run_id": run_id,
                    "seq": seq,
                    "event_type": event_type,
                    "node": node,
                    "node_attempt": node_attempt,
                    "call_id": call_id,
                    "payload_digest": payload_row.cipher_digest if payload_row is not None else "",
                    "metadata": meta,
                }
                event = AgentRunEvent(
                    event_id=event_id,
                    run_id=run_id,
                    seq=seq,
                    schema_version=self.settings.RUN_EVENT_SCHEMA_VERSION,
                    event_type=event_type,
                    node=node,
                    node_attempt=node_attempt,
                    call_id=call_id,
                    causation_id=causation_id,
                    payload_ref=payload_row.opaque_id if payload_row is not None else None,
                    metadata_json=meta,
                    prev_hash=prev_hash,
                    event_hash=_hash_event(prev_hash, envelope),
                )
                self.db.add(event)
                result = self.db.execute(
                    update(AgentRun)
                    .where(AgentRun.run_id == run_id, AgentRun.last_seq == run.last_seq)
                    .values(last_seq=seq)
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise RunConflict("事件序号并发冲突")
                if commit:
                    self.db.commit()
                else:
                    self.db.flush()
                return event
            except (IntegrityError, OperationalError, RunConflict) as exc:
                last_error = exc
                self.db.rollback()
                if storage_key:
                    self.store.discard_file(storage_key)
                time.sleep(0.01 * (attempt + 1))
                continue
            except Exception:
                self.db.rollback()
                if storage_key:
                    self.store.discard_file(storage_key)
                raise
        raise RunConflict("事件追加冲突重试耗尽") from last_error

    # ---------------- 权限与查询 ----------------
    @staticmethod
    def _has_inspect(db: Session, actor) -> bool:
        if actor is None:
            return False
        try:
            return "run.inspect" in role_permissions(db, actor.role_id, fresh=True)
        except Exception:  # noqa: BLE001 - 权限查询失败按拒绝处理
            return False

    def _check_view(self, run: AgentRun, actor) -> None:
        if actor is None:
            raise RunAccessDenied("缺少主体")
        if run.domain_id not in (actor.domain_scope or []):
            raise RunAccessDenied("无权查看该运行")
        if actor.user_id == run.owner_id:
            return
        if self._has_inspect(self.db, actor):
            return
        raise RunAccessDenied("无权查看该运行")

    def payload_available(self, payload_ref: str | None) -> bool:
        if not payload_ref:
            return False
        row = self.db.get(RunPayload, payload_ref)
        return bool(row is not None and row.deletion_state == "available" and row.key_ref)

    def get_run_view(self, run_id: str, actor) -> dict[str, Any]:
        run = self.get_run(run_id)
        self._check_view(run, actor)
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "owner_id": run.owner_id,
            "domain_id": run.domain_id,
            "mode": run.mode,
            "status": run.status,
            "status_version": run.status_version,
            "last_seq": run.last_seq,
            "manifest": run.manifest,
            "privacy_state": run.privacy_state,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "replayable": False,
            "replay_blocked_reason": "frozen 回放属于 05-B，尚未开放",
        }

    def list_events(
        self, run_id: str, actor, *, after_seq: int = 0, limit: int | None = None
    ) -> list[dict[str, Any]]:
        run = self.get_run(run_id)
        self._check_view(run, actor)
        page = max(1, min(limit or self.settings.RUN_EVENT_PAGE_MAX, self.settings.RUN_EVENT_PAGE_MAX))
        rows = (
            self.db.execute(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == run_id, AgentRunEvent.seq > after_seq)
                .order_by(AgentRunEvent.seq.asc())
                .limit(page)
            )
            .scalars()
            .all()
        )
        return [self._event_dto(event) for event in rows]

    def _event_dto(self, event: AgentRunEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "seq": event.seq,
            "schema_version": event.schema_version,
            "event_type": event.event_type,
            "node": event.node,
            "node_attempt": event.node_attempt,
            "call_id": event.call_id,
            "causation_id": event.causation_id,
            "metadata": event.metadata_json or {},
            "payload_available": self.payload_available(event.payload_ref),
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }

    def verify_chain(self, run_id: str) -> dict[str, Any]:
        events = (
            self.db.execute(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == run_id)
                .order_by(AgentRunEvent.seq.asc())
            )
            .scalars()
            .all()
        )
        prev_hash = ""
        for index, event in enumerate(events, start=1):
            payload_digest = ""
            if event.payload_ref:
                payload = self.db.get(RunPayload, event.payload_ref)
                payload_digest = payload.cipher_digest if payload is not None else ""
            envelope = {
                "run_id": event.run_id,
                "seq": event.seq,
                "event_type": event.event_type,
                "node": event.node,
                "node_attempt": event.node_attempt,
                "call_id": event.call_id,
                "payload_digest": payload_digest,
                "metadata": event.metadata_json or {},
            }
            expected = _hash_event(prev_hash, envelope)
            if event.prev_hash != prev_hash or event.event_hash != expected:
                return {"valid": False, "checked": index, "first_invalid_seq": event.seq}
            prev_hash = event.event_hash
        return {"valid": True, "checked": len(events), "first_invalid_seq": None}

    def inspect(self, run_id: str, actor) -> dict[str, Any]:
        """纯事件 reducer：只读取已提交事件与安全元数据，不调用编排/模型/工具。"""
        run_view = self.get_run_view(run_id, actor)
        events: list[dict[str, Any]] = []
        after_seq = 0
        while True:
            page = self.list_events(
                run_id, actor, after_seq=after_seq, limit=self.settings.RUN_EVENT_PAGE_MAX
            )
            events.extend(page)
            if len(page) < self.settings.RUN_EVENT_PAGE_MAX:
                break
            after_seq = page[-1]["seq"]
        timeline = [
            {
                "seq": event["seq"],
                "event_type": event["event_type"],
                "node": event["node"],
                "status": "waiting" if event["event_type"].endswith(".requested") else "done",
                "summary": event["metadata"].get("summary", ""),
                "metadata": event["metadata"],
                "payload_available": event["payload_available"],
                "created_at": event["created_at"],
            }
            for event in events
        ]
        unsupported = sorted(
            {
                event["schema_version"]
                for event in events
                if event["schema_version"] != self.settings.RUN_EVENT_SCHEMA_VERSION
            }
        )
        return {
            "run": run_view,
            "timeline": timeline,
            "integrity": self.verify_chain(run_id),
            "compatible": not unsupported,
            "unsupported_schema_versions": unsupported,
            "legacy": False,
        }

    def save_state_checkpoint(
        self, run_id: str, state_dict: dict[str, Any], *, node: str, next_node: str
    ) -> AgentRunCheckpoint:
        """节点快照：完整 TurnState 加密落载荷，checkpoint 只存引用（05-B）。"""
        run = self.get_run(run_id)
        # 快照必须保留原始键序（composer 按 dict 顺序渲染），不能使用排序 JSON
        payload = self.store.put(
            json.dumps(state_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            owner_id=run.owner_id,
            domain_id=run.domain_id,
            classification="sensitive",
            sources=[("run", run_id)],
            retention_days=self.settings.RUN_PAYLOAD_RETENTION_DAYS,
        )
        checkpoint = AgentRunCheckpoint(
            checkpoint_id=f"CHK-{uuid.uuid4().hex[:16]}",
            run_id=run_id,
            after_seq=run.last_seq,
            next_node=next_node,
            state_ref=payload.opaque_id,
            checkpoint_kind=node,
        )
        self.db.add(checkpoint)
        self.db.commit()
        return checkpoint

    def latest_checkpoint(self, run_id: str, *, next_node: str | None = None) -> AgentRunCheckpoint | None:
        stmt = select(AgentRunCheckpoint).where(AgentRunCheckpoint.run_id == run_id)
        if next_node is not None:
            stmt = stmt.where(AgentRunCheckpoint.next_node == next_node)
        return self.db.execute(stmt.order_by(AgentRunCheckpoint.created_at.desc())).scalars().first()

    def load_checkpoint_state(self, checkpoint: AgentRunCheckpoint) -> dict[str, Any]:
        return self.store.load_json(checkpoint.state_ref)

    def cancel(self, run_id: str, actor) -> AgentRun:
        run = self.get_run(run_id)
        self._check_view(run, actor)
        if actor.user_id != run.owner_id and "run.cancel" not in role_permissions(
            self.db, actor.role_id, fresh=True
        ):
            raise RunAccessDenied("无权取消该运行")
        if run.status in ("completed", "failed", "cancelled"):
            raise RunConflict(f"运行已处于 {run.status}，不可取消")
        return self.set_status(run_id, "cancelled", event_type="run.cancelled")

    def _all_events(self, run_id: str) -> list[AgentRunEvent]:
        return list(
            self.db.execute(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == run_id)
                .order_by(AgentRunEvent.seq.asc())
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _run_summary(run: AgentRun, events: list[AgentRunEvent]) -> dict[str, Any]:
        total_score = None
        citations: set[str] = set()
        conflicts: set[str] = set()
        claims = None
        text_digest = ""
        for event in events:
            meta = event.metadata_json or {}
            if event.event_type == "evaluation.completed":
                total_score = meta.get("total_score")
            elif event.event_type == "retrieval.completed":
                citations.update(
                    str(item.get("chunk_id")) for item in meta.get("citations", []) if item.get("chunk_id")
                )
            elif event.event_type == "fusion.completed":
                conflicts.update(str(item) for item in meta.get("conflict_ids", []))
            elif event.event_type == "draft.created":
                claims = meta.get("claims")
            elif event.event_type == "output.ready":
                text_digest = str(meta.get("text_digest") or "")
        return {
            "run_id": run.run_id,
            "status": run.status,
            "mode": run.mode,
            "total_score": total_score,
            "claims": claims,
            "output_digest": text_digest,
            "citations": sorted(citations),
            "conflicts": sorted(conflicts),
            "event_count": len(events),
        }

    def diff(self, left_id: str, right_id: str, actor) -> dict[str, Any]:
        """双运行可见性共同范围 diff：只返回安全摘要，不返回原始载荷。"""
        left = self.get_run(left_id)
        right = self.get_run(right_id)
        self._check_view(left, actor)
        self._check_view(right, actor)
        left_events = self._all_events(left_id)
        right_events = self._all_events(right_id)
        left_summary = self._run_summary(left, left_events)
        right_summary = self._run_summary(right, right_events)
        left_citations = set(left_summary["citations"])
        right_citations = set(right_summary["citations"])
        left_conflicts = set(left_summary["conflicts"])
        right_conflicts = set(right_summary["conflicts"])
        differences = {
            key: {"left": left_summary[key], "right": right_summary[key]}
            for key in ("status", "total_score", "claims", "output_digest", "event_count")
            if left_summary[key] != right_summary[key]
        }
        return {
            "left": left_summary,
            "right": right_summary,
            "differences": differences,
            "citations": {
                "shared": sorted(left_citations & right_citations),
                "left_only": sorted(left_citations - right_citations),
                "right_only": sorted(right_citations - left_citations),
            },
            "conflicts": {
                "shared": sorted(left_conflicts & right_conflicts),
                "left_only": sorted(left_conflicts - right_conflicts),
                "right_only": sorted(right_conflicts - left_conflicts),
            },
            "visibility": "common",
        }

    def delete_payload(self, opaque_id: str, actor) -> bool:
        """载荷删除：校验 owner 与领域；删除后事件保留但标记不可读。"""
        row = self.db.get(RunPayload, opaque_id)
        if row is None:
            raise KeyError("载荷不存在")
        if row.owner_id != actor.user_id or row.domain_id not in (actor.domain_scope or []):
            raise RunAccessDenied("无权删除该载荷")
        deleted = self.store.delete(opaque_id)
        self.db.commit()
        return deleted

    def load_payload(self, opaque_id: str, actor) -> bytes:
        row = self.db.get(RunPayload, opaque_id)
        if row is None:
            raise KeyError("载荷不存在")
        if row.owner_id != actor.user_id and not self._has_inspect(self.db, actor):
            raise RunAccessDenied("无权读取该载荷")
        if row.domain_id not in (actor.domain_scope or []):
            raise RunAccessDenied("无权读取该载荷")
        try:
            return self.store.load(opaque_id)
        except PayloadUnavailable as exc:
            raise KeyError("载荷不可用（已删除或已过期）") from exc
