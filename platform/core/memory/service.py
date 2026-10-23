"""记忆中心（M3，技术方案 03 §3.3）：四层记忆读写、晋升、衰减、压缩。

- 会话级：对话消息表 + 滑窗（由会话存储承担）；
- 用户级：提取器产出候选 → ⑤ Hook → 写入；向量相似度 >0.9 合并更新；
- 企业级：晋升（多人验证/管理员审核）+ ⑤ Hook；可见性按 permission_scope；
- 领域级：企业级高频模式压缩固化（周期任务）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from adapters.storage_sqlite import cosine
from core.abstraction.dtos import Actor, MemoryCandidate, MemoryItemRecord
from core.abstraction.enums import MemoryLayer, MemoryStatus
from core.clock import utc_now
from core.guardrails.hooks import HookContext
from core.guardrails.memory_hook import MemoryWriteGuard
from core.runtime import get_domain_registry, get_model_gateway, memory_repo

logger = logging.getLogger(__name__)

SIMILARITY_MERGE_THRESHOLD = 0.9


@dataclass
class RecallCtx:
    actor: Actor | None = None
    domain_id: str = ""
    query: str = ""
    query_vec: list[float] | None = None
    session_id: str = ""
    k_user: int = 3
    k_enterprise: int = 3
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriteResult:
    accepted: bool
    memory_id: str = ""
    merged: bool = False
    message: str = ""


class MemoryService:
    def __init__(self, db: Session, gateway=None, registry=None):
        self.db = db
        self.gateway = gateway or get_model_gateway()
        self.registry = registry or get_domain_registry()
        self.repo = memory_repo(db)
        self.guard = MemoryWriteGuard(db)

    # ---------------- 读取（分层 top-k 注入） ----------------
    async def recall(self, ctx: RecallCtx) -> list[MemoryItemRecord]:
        items: list[MemoryItemRecord] = []
        if ctx.actor:
            items.extend(
                self._recall_layer(
                    MemoryLayer.USER,
                    owner_id=ctx.actor.user_id,
                    ctx=ctx,
                    limit=ctx.k_user,
                    domain_id=ctx.domain_id or None,
                )
            )
        enterprise = [
            item
            for item in self.repo.list_items(layer=MemoryLayer.ENTERPRISE, domain_id=ctx.domain_id or None, status="active", limit=50)
            if item.permission_scope in ("all", "domain")
        ]
        items.extend(self._rank_and_take(enterprise, ctx, ctx.k_enterprise))
        domain_items = self.repo.list_items(layer=MemoryLayer.DOMAIN, domain_id=ctx.domain_id or None, status="active", limit=20)
        items.extend(self._rank_and_take(domain_items, ctx, 2))
        # 复用计数与 last_used_at（衰减依据）
        for item in items:
            self._touch(item)
        self.db.commit()
        return items

    def _recall_layer(
        self, layer: str, owner_id: str, ctx: RecallCtx, limit: int, domain_id: str | None = None
    ) -> list[MemoryItemRecord]:
        """个人记忆召回同时限定 owner + domain，避免跨领域串用。"""
        candidates = self.repo.list_items(
            layer=layer, owner_id=owner_id, domain_id=domain_id, status="active", limit=100
        )
        return self._rank_and_take(candidates, ctx, limit)

    def _rank_and_take(self, candidates: list[MemoryItemRecord], ctx: RecallCtx, limit: int) -> list[MemoryItemRecord]:
        if not candidates:
            return []
        if ctx.query_vec:
            ranked = []
            for item in candidates:
                sim = cosine(ctx.query_vec, item.embedding or []) if item.embedding else 0.0
                score = sim * 0.7 + min(item.reuse_count / 10, 1.0) * 0.15 + item.confidence * 0.15
                ranked.append((score, item))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            return [item for _, item in ranked[:limit]]
        candidates.sort(key=lambda item: (item.reuse_count, item.confidence), reverse=True)
        return candidates[:limit]

    def _touch(self, item: MemoryItemRecord) -> None:
        from core.memory.models import MemItem

        row = self.db.get(MemItem, item.memory_id)
        if row is None:
            return
        row.reuse_count = (row.reuse_count or 0) + 1
        row.last_used_at = utc_now()

    # ---------------- 写入（经 ⑤ Hook） ----------------
    async def write(self, candidate: MemoryCandidate, ctx: HookContext) -> WriteResult:
        decision = self.guard.check(candidate, ctx)
        self.db.commit()
        if decision.blocked:
            return WriteResult(accepted=False, message=decision.message)

        embedding = None
        try:
            vectors = await self.gateway.embed([candidate.content])
            embedding = vectors[0] if vectors else None
        except Exception:
            logger.warning("memory embedding failed, store lexical-only", exc_info=True)

        # 相似记忆合并（>0.9 合并更新：reuse_count+1）
        if embedding and candidate.layer == MemoryLayer.USER:
            similar = self.repo.similar_items(
                candidate.owner_id,
                candidate.layer,
                embedding,
                SIMILARITY_MERGE_THRESHOLD,
                domain_id=candidate.domain_id,
            )
            if similar:
                existing = similar[0]
                existing.reuse_count += 1
                existing.confidence = min(1.0, existing.confidence + 0.05)
                from core.clock import iso as _iso

                existing.last_used_at = _iso(utc_now())
                self.repo.upsert_item(existing)
                self.repo.add_event(existing.memory_id, "merge", {"new_content": candidate.content[:200]}, ctx.actor.user_id if ctx.actor else None)
                self.db.commit()
                return WriteResult(accepted=True, memory_id=existing.memory_id, merged=True, message="相似记忆已合并更新")

        memory_id = f"MEM-{uuid.uuid4().hex[:12]}"
        item = MemoryItemRecord(
            memory_id=memory_id,
            layer=candidate.layer,
            owner_id=candidate.owner_id,
            content=candidate.content,
            domain_id=candidate.domain_id,
            permission_scope=candidate.permission_scope,
            source_type=candidate.source_type,
            confidence=candidate.confidence,
            embedding=embedding,
            meta={**(candidate.meta or {}), "origin": candidate.origin},
        )
        self.repo.upsert_item(item)
        self.repo.add_event(memory_id, "apply", {"origin": candidate.origin, "layer": candidate.layer}, ctx.actor.user_id if ctx.actor else None)
        self.db.commit()
        return WriteResult(accepted=True, memory_id=memory_id, message="记忆已写入")

    # ---------------- 晋升（个人→企业，BR-06） ----------------
    @staticmethod
    def _normalized_key(content: str | None) -> str:
        return " ".join((content or "").split()).lower()[:200]

    def _verified_group(self, item: MemoryItemRecord) -> list[MemoryItemRecord]:
        """同领域、同规范化事实的个人记忆分组（用于独立主体验证判定）。"""
        key = self._normalized_key(item.content)
        if not key:
            return []
        same_domain = self.repo.list_items(
            layer=MemoryLayer.USER, domain_id=item.domain_id, status="active", limit=500
        )
        return [row for row in same_domain if self._normalized_key(row.content) == key]

    def promote(self, memory_id: str, basis: str, actor: Actor | None) -> MemoryItemRecord:
        """晋升：≥2 名不同主体同向验证后，生成新的企业级记忆（不覆盖原个人条目）。"""
        item = self.repo.get_item(memory_id)
        if item is None:
            raise KeyError(f"记忆不存在: {memory_id}")
        if item.layer != MemoryLayer.USER:
            raise ValueError("仅个人记忆可晋升为企业级")
        group = self._verified_group(item)
        owners = sorted({row.owner_id for row in group})
        if len(owners) < 2:
            raise ValueError("晋升需至少两名不同授权主体的同向验证")
        if not (basis or "").strip():
            raise ValueError("晋升需提供审核依据")
        new_id = f"MEM-{uuid.uuid4().hex[:12]}"
        promoted = replace(
            item,
            memory_id=new_id,
            layer=MemoryLayer.ENTERPRISE,
            permission_scope="domain",
            promoted_from=memory_id,
            promotion_basis=basis,
            source_type="confirmed",
            confidence=min(1.0, max(item.confidence, 0.8)),
            derived_from=[row.memory_id for row in group],
            meta={
                **(item.meta or {}),
                "promotion_basis": basis,
                "verified_by": owners,
            },
        )
        self.repo.upsert_item(promoted)
        self.repo.add_event(
            new_id,
            "promote",
            {"basis": basis, "from": memory_id, "verified_by": owners, "to": MemoryLayer.ENTERPRISE},
            actor.user_id if actor else None,
        )
        self.db.commit()
        return promoted

    def promotion_candidates(self, domain_id: str, min_distinct_users: int = 2) -> list[dict[str, Any]]:
        """晋升候选：仅按不同主体数判定；单人多次复用/召回不构成多人验证。"""
        items = self.repo.list_items(layer=MemoryLayer.USER, domain_id=domain_id, status="active", limit=500)
        grouped: dict[str, list[MemoryItemRecord]] = {}
        for item in items:
            key = self._normalized_key(item.content)
            if key:
                grouped.setdefault(key, []).append(item)
        candidates: list[dict[str, Any]] = []
        for key, group in grouped.items():
            owners = {item.owner_id for item in group}
            if len(owners) < min_distinct_users:
                continue
            total_reuse = sum(item.reuse_count for item in group)
            candidates.append(
                {
                    "content": key,
                    "distinct_users": len(owners),
                    "reuse_count": total_reuse,
                    "memory_ids": [item.memory_id for item in group],
                    "basis": f"{len(owners)} 名不同用户同向验证",
                }
            )
        return candidates

    # ---------------- 衰减与压缩（BR-07，Celery 周期任务） ----------------
    def decay(self, domain_id: str | None = None) -> dict[str, int]:
        now = utc_now()
        stats = {"decayed": 0, "archived": 0}
        items = self.repo.list_items(domain_id=domain_id, status="active", limit=1000)
        for item in items:
            if item.layer == MemoryLayer.SESSION:
                continue
            last = item.last_used_at or None
            if not last:
                continue
            from datetime import datetime

            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                continue
            age_days = (now - last_dt).days
            factor = 1.0
            if age_days > 365 * 2:
                factor = 0.5
            elif age_days > 365:
                factor = 0.8
            if factor < 1.0:
                new_conf = round(item.confidence * factor, 3)
                updated = MemoryItemRecord(**{**item.__dict__})
                updated.confidence = new_conf
                if new_conf < 0.3:
                    updated.status = MemoryStatus.ARCHIVED
                    stats["archived"] += 1
                    self.repo.add_event(item.memory_id, "archive", {"confidence": new_conf}, None)
                else:
                    stats["decayed"] += 1
                    self.repo.add_event(item.memory_id, "decay", {"factor": factor, "confidence": new_conf}, None)
                self.repo.upsert_item(updated)
        self.db.commit()
        return stats

    def compact(self, domain_id: str, min_group: int = 2) -> list[MemoryItemRecord]:
        """压缩摘要：低层记忆聚类生成高层「模式记忆」（derived_from 溯源链）。"""
        items = self.repo.list_items(layer=MemoryLayer.ENTERPRISE, domain_id=domain_id, status="active", limit=100)
        if len(items) < min_group:
            return []
        digest = "；".join(item.content[:60] for item in items[: min(len(items), 8)])
        memory_id = f"MEM-{uuid.uuid4().hex[:12]}"
        pattern = MemoryItemRecord(
            memory_id=memory_id,
            layer=MemoryLayer.DOMAIN,
            owner_id="platform",
            content=f"领域模式记忆（{domain_id}）：{digest}",
            domain_id=domain_id,
            permission_scope="domain",
            source_type="ai_inferred",
            confidence=0.7,
            derived_from=[item.memory_id for item in items],
            meta={"type": "pattern", "auto": True},
        )
        self.repo.upsert_item(pattern)
        self.repo.add_event(memory_id, "apply", {"compact_from": len(items)}, None)
        self.db.commit()
        return [pattern]

    # ---------------- PIPL 个人记忆（查阅/更正/删除/导出） ----------------
    def list_mine(self, user_id: str) -> list[MemoryItemRecord]:
        return self.repo.list_items(layer=MemoryLayer.USER, owner_id=user_id, status=None, limit=200)

    async def update_mine(self, user_id: str, memory_id: str, content: str) -> MemoryItemRecord:
        """更正本人记忆：重算向量并递增版本，旧向量不再可命中；仅本人 USER 层可改。"""
        item = self.repo.get_item(memory_id)
        if item is None or item.owner_id != user_id or item.layer != MemoryLayer.USER:
            raise KeyError("记忆不存在或无权访问")
        embedding: list[float] | None = None
        try:
            vectors = await self.gateway.embed([content])
            embedding = vectors[0] if vectors else None
        except Exception:
            logger.warning("memory re-embed failed, clear stale vector", exc_info=True)
        meta = dict(item.meta or {})
        version = int(meta.get("version", 1)) + 1
        meta.update({"version": version, "corrected_by": user_id})
        updated = replace(item, content=content, embedding=embedding, meta=meta)
        self.repo.upsert_item(updated)
        self.repo.add_event(
            memory_id,
            "update",
            {"version": version, "reembedded": embedding is not None, "content_length": len(content)},
            user_id,
        )
        self.db.commit()
        return updated

    def delete_mine(self, user_id: str, memory_id: str) -> None:
        """删除本人记忆：审计不保留原文；以其为来源的派生条目同步失效。"""
        item = self.repo.get_item(memory_id)
        if item is None or item.owner_id != user_id:
            raise KeyError("记忆不存在或无权访问")
        self.repo.add_event(memory_id, "delete", {"content_length": len(item.content or "")}, user_id)
        self.repo.delete_item(memory_id)
        for derived in self.repo.list_items(status="active", limit=1000):
            if memory_id in (derived.derived_from or []):
                invalidated = replace(derived, status="deleted")
                self.repo.upsert_item(invalidated)
                self.repo.add_event(
                    derived.memory_id, "invalidate", {"source_deleted": memory_id}, user_id
                )
        self.db.commit()
