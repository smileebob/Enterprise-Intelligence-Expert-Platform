"""自成长中心（M7，技术方案 03 §3.7）：反馈采集 → 正/负样本 → 案例库/纠错规则 → 标准更新状态机。

- 正样本（采纳）→ 案例库（kb 文档 source_type=confirmed，status=pending 待审核，BR-06 防污染）；
- 负样本（修正/否决）→ 纠错规则（注入编排 System 负向约束）；
- 标准更新：draft → 审核发布 active（旧版 archived；BR-09 不自动重算历史评分）；
- 标注数据导出（P2 预留，本次不训练模型）。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.tokenize import token_text
from core.abstraction.dtos import Actor, AuditEntry, EvalStandardRecord
from core.abstraction.enums import FeedbackRating
from core.growth.models import GrowthCorrectionRule, GrowthFeedback
from core.runtime import audit_repo, eval_repo, knowledge_repo

logger = logging.getLogger(__name__)


class GrowthService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------- 反馈采集与样本分流 ----------------
    def record_feedback(
        self,
        *,
        user_id: str,
        domain_id: str,
        rating: str,
        session_id: str | None = None,
        message_id: str | None = None,
        eval_id: str | None = None,
        correction: str = "",
        comment: str = "",
        trace_id: str | None = None,
        message_content: str = "",
        citations: list[dict[str, Any]] | None = None,
        actor: Actor | None = None,
    ) -> dict[str, Any]:
        sample_type = {
            FeedbackRating.ADOPTED: "positive",
            FeedbackRating.REJECTED: "negative",
            FeedbackRating.MODIFIED: "correction",
        }.get(rating, "negative")
        feedback = GrowthFeedback(
            feedback_id=f"FB-{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            message_id=message_id,
            eval_id=eval_id,
            user_id=user_id,
            domain_id=domain_id,
            rating=rating,
            correction=correction,
            comment=comment,
            trace_id=trace_id,
            sample_type=sample_type,
        )
        self.db.add(feedback)

        case_doc_id = None
        rule_id = None
        if rating == FeedbackRating.ADOPTED and message_content:
            case_doc_id = self._promote_case(feedback.feedback_id, domain_id, message_content, citations or [])
            feedback.case_doc_id = case_doc_id
        elif rating in (FeedbackRating.REJECTED, FeedbackRating.MODIFIED) and (correction or comment):
            rule_id = self._derive_correction_rule(feedback.feedback_id, domain_id, rating, correction or comment)

        audit_repo(self.db).append(
            AuditEntry(
                trace_id=trace_id or "",
                actor_id=user_id,
                action="growth.feedback",
                resource=message_id or eval_id or "",
                detail={"rating": rating, "sample_type": sample_type, "case_doc_id": case_doc_id, "rule_id": rule_id},
            )
        )
        self.db.commit()
        return {
            "feedback_id": feedback.feedback_id,
            "sample_type": sample_type,
            "case_doc_id": case_doc_id,
            "rule_id": rule_id,
        }

    def _promote_case(self, feedback_id: str, domain_id: str, content: str, citations: list[dict[str, Any]]) -> str:
        """正样本 → 案例库（confirmed 层，待知识管理员审核后 ready；BR-06 防污染）。"""
        doc_id = f"doc:cases:{feedback_id}"
        repo = knowledge_repo(self.db)
        from core.abstraction.dtos import KbChunkRecord, KbDocumentRecord

        repo.upsert_documents(
            [
                KbDocumentRecord(
                    doc_id=doc_id,
                    title=f"采纳案例 {feedback_id}",
                    domain_id=domain_id,
                    modality="text",
                    source_type="confirmed",
                    permission_level=2,
                    origin="自成长/正样本",
                    meta={"feedback_id": feedback_id, "citations": citations},
                )
            ]
        )
        repo.upsert_chunks(
            [
                KbChunkRecord(
                    chunk_id=f"chk:cases:{feedback_id}",
                    doc_id=doc_id,
                    seq=0,
                    content=content,
                    domain_id=domain_id,
                    source_type="confirmed",
                    confidence=0.9,
                    permission_level=2,
                    source_ref=f"采纳案例/{feedback_id}",
                    status="pending",  # 待审核（审核通过后才参与检索）
                    tokens=token_text(content),
                    meta={"record_id": feedback_id, "record_type": "cases", "review": "pending"},
                )
            ]
        )
        self.db.flush()
        return doc_id

    def _derive_correction_rule(self, feedback_id: str, domain_id: str, rating: str, text: str) -> str:
        """原始反馈先落 pending 文本规则；管理员显式转为结构化规则后才可执行。"""
        rule_id = f"RULE-{uuid.uuid4().hex[:10]}"
        rule_text = f"[{'否决' if rating == FeedbackRating.REJECTED else '修正'}样本] {text.strip()[:200]}"
        self.db.add(
            GrowthCorrectionRule(
                rule_id=rule_id,
                domain_id=domain_id,
                rule_text=rule_text,
                rule_type="text_only",
                source_feedback=feedback_id,
                status="pending",
            )
        )
        self.db.flush()
        return rule_id

    def list_correction_rules(
        self, domain_id: str, limit: int = 20, status: str | None = "active"
    ) -> list[dict[str, Any]]:
        stmt = select(GrowthCorrectionRule).where(GrowthCorrectionRule.domain_id == domain_id)
        if status:
            stmt = stmt.where(GrowthCorrectionRule.status == status)
        rows = (
            self.db.execute(stmt.order_by(GrowthCorrectionRule.created_at.desc()).limit(limit))
            .scalars()
            .all()
        )
        return [
            {
                "rule_id": r.rule_id,
                "rule_text": r.rule_text,
                "rule_type": r.rule_type,
                "target_fact": r.target_fact,
                "scope": r.scope,
                "status": r.status,
                "approved_by": r.approved_by,
                "used_count": r.used_count,
            }
            for r in rows
        ]

    def approve_rule(
        self,
        rule_id: str,
        *,
        rule_type: str,
        target_fact: str,
        rule_text: str,
        scope: str,
        actor: Actor | None,
    ) -> dict[str, Any]:
        """人工把 pending 反馈转为结构化规则并发布；禁止原文直接当指令执行。"""
        allowed = {"text_only", "forbid_claim", "add_verification", "order_preference"}
        if rule_type not in allowed:
            raise ValueError(f"未知规则类型: {rule_type}")
        if rule_type == "forbid_claim" and not target_fact.strip():
            raise ValueError("forbid_claim 必须提供 target_fact")
        row = self.db.get(GrowthCorrectionRule, rule_id)
        if row is None:
            raise KeyError(f"规则不存在: {rule_id}")
        if row.status != "pending":
            raise ValueError(f"规则状态为 {row.status}，不可再次审核")
        row.rule_type = rule_type
        row.target_fact = target_fact.strip()
        row.rule_text = rule_text.strip() or row.rule_text
        row.scope = scope.strip()
        row.status = "active"
        row.approved_by = actor.user_id if actor else None
        from core.clock import utc_now

        row.approved_at = utc_now()
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action="growth.rule.approve",
                resource=rule_id,
                detail={"rule_type": rule_type, "target_fact": row.target_fact, "scope": scope},
            )
        )
        self.db.commit()
        return {
            "rule_id": rule_id,
            "status": row.status,
            "rule_type": row.rule_type,
            "target_fact": row.target_fact,
        }

    def reject_rule(self, rule_id: str, actor: Actor | None) -> dict[str, Any]:
        row = self.db.get(GrowthCorrectionRule, rule_id)
        if row is None:
            raise KeyError(f"规则不存在: {rule_id}")
        if row.status != "pending":
            raise ValueError(f"规则状态为 {row.status}，不可再次审核")
        row.status = "rejected"
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action="growth.rule.reject",
                resource=rule_id,
                detail={},
            )
        )
        self.db.commit()
        return {"rule_id": rule_id, "status": "rejected"}

    def active_rules(self, domain_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.list_correction_rules(domain_id, limit=limit, status="active")

    def active_rule_texts(self, domain_id: str, limit: int = 3) -> list[str]:
        return [r["rule_text"] for r in self.active_rules(domain_id, limit)]

    def mark_rules_used(self, rule_ids: list[str]) -> None:
        if not rule_ids:
            return
        from core.clock import utc_now

        rows = (
            self.db.execute(select(GrowthCorrectionRule).where(GrowthCorrectionRule.rule_id.in_(rule_ids)))
            .scalars()
            .all()
        )
        for row in rows:
            row.used_count = (row.used_count or 0) + 1
            row.approved_at = row.approved_at or utc_now()
        self.db.commit()

    # ---------------- 案例审核（F2 审核队列） ----------------
    def review_queue(self, domain_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = (
            self.db.execute(
                select(GrowthFeedback)
                .where(GrowthFeedback.domain_id == domain_id, GrowthFeedback.case_doc_id.is_not(None))
                .order_by(GrowthFeedback.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        repo = knowledge_repo(self.db)
        items = []
        for row in rows:
            chunk = repo.get_chunk(f"chk:cases:{row.feedback_id}")
            if chunk is None:
                continue
            items.append(
                {
                    "feedback_id": row.feedback_id,
                    "doc_id": row.case_doc_id,
                    "chunk_id": chunk.chunk_id,
                    "status": chunk.status,
                    "content_preview": chunk.content[:200],
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        return items

    def review_case(self, feedback_id: str, action: str, reviewer: Actor | None) -> dict[str, Any]:
        """审核通过 → ready（参与检索）；驳回 → rejected。"""
        repo = knowledge_repo(self.db)
        chunk_id = f"chk:cases:{feedback_id}"
        chunk = repo.get_chunk(chunk_id)
        if chunk is None:
            raise KeyError(f"案例不存在: {feedback_id}")
        from core.knowledge.models import KbChunk

        row = self.db.get(KbChunk, chunk_id)
        new_status = "ready" if action == "approve" else "rejected"
        row.status = new_status
        meta = dict(row.meta or {})
        meta["review"] = new_status
        meta["reviewed_by"] = reviewer.user_id if reviewer else ""
        row.meta = meta
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=reviewer.user_id if reviewer else None,
                action=f"growth.case.{action}",
                resource=feedback_id,
                detail={"status": new_status},
            )
        )
        self.db.commit()
        return {"feedback_id": feedback_id, "status": new_status}

    # ---------------- 标准更新状态机（draft → active → archived） ----------------
    def propose_standard_update(
        self,
        domain_id: str,
        actor: Actor | None,
        note: str = "",
        patch: dict[str, Any] | None = None,
        rationale: str = "",
    ) -> dict[str, Any]:
        """标准草稿必须含显式 patch；权重/维度校验通过才落库（06-B §3.3）。"""
        repo = eval_repo(self.db)
        current = repo.get_active_standard(domain_id, "solution") or repo.get_active_standard(domain_id, "enterprise_profile")
        if current is None:
            raise KeyError(f"领域 {domain_id} 无现行标准")
        weights = dict(current.dimension_weights)
        scenario_profiles = dict(current.scenario_profiles)
        if patch is not None:
            candidate_weights = patch.get("dimension_weights")
            if candidate_weights is not None:
                if not isinstance(candidate_weights, dict) or not candidate_weights:
                    raise ValueError("dimension_weights 必须为非空对象")
                unknown = [code for code in candidate_weights if code not in weights]
                if unknown:
                    raise ValueError(f"未知评分维度: {'、'.join(unknown)}")
                if any(not isinstance(value, (int, float)) or value < 0 for value in candidate_weights.values()):
                    raise ValueError("维度权重必须为非负数")
                if sum(candidate_weights.values()) != 100:
                    raise ValueError("维度权重合计必须为 100")
                weights = {code: int(value) for code, value in candidate_weights.items()}
            if patch.get("scenario_profiles"):
                scenario_profiles = dict(patch["scenario_profiles"])
        if patch is not None and not (rationale or note).strip():
            raise ValueError("标准变更必须提供理由")
        stats = self._feedback_stats(domain_id)
        next_v = self._next_version(current.version)
        stem = re.sub(r"-v\d+$", "", current.standard_id) or f"std-{current.year}-{domain_id}"
        new_id = f"{stem}-v{next_v}"
        draft = EvalStandardRecord(
            standard_id=new_id,
            domain_id=current.domain_id,
            year=current.year,
            object_type=current.object_type,
            sub_domain=current.sub_domain,
            standard_desc=current.standard_desc,
            dimension_weights=weights,
            scoring_rubric=current.scoring_rubric,
            scenario_profiles=scenario_profiles,
            sample_size=stats["total"],
            version=new_id,
            status="draft",
            meta={
                "proposed_from": current.standard_id,
                "note": note,
                "rationale": rationale or note,
                "patch": patch or {},
                "feedback_stats": stats,
            },
        )
        repo.upsert_standard(draft)
        self.db.commit()
        return {"standard_id": draft.standard_id, "status": "draft", "feedback_stats": stats}

    def _feedback_stats(self, domain_id: str) -> dict[str, int]:
        rows = self.db.execute(select(GrowthFeedback).where(GrowthFeedback.domain_id == domain_id)).scalars().all()
        adopted = sum(1 for r in rows if r.rating == "adopted")
        rejected = sum(1 for r in rows if r.rating == "rejected")
        modified = sum(1 for r in rows if r.rating == "modified")
        return {"total": len(rows), "adopted": adopted, "rejected": rejected, "modified": modified}

    @staticmethod
    def _next_version(version: str) -> int:
        try:
            return int(str(version).rsplit("v", 1)[-1]) + 1
        except (ValueError, IndexError):
            return 2

    def publish_standard(self, draft_id: str, reviewer: str, actor: Actor | None) -> dict[str, Any]:
        """发布新版本：旧版 archived（历史评分不重算，BR-09）。"""
        repo = eval_repo(self.db)
        draft = repo.get_standard(draft_id)
        if draft is None:
            raise KeyError(f"标准草稿不存在: {draft_id}")
        if draft.status != "draft":
            raise ValueError("仅 draft 状态可发布")
        weights = draft.dimension_weights or {}
        if not weights or any(value < 0 for value in weights.values()) or sum(weights.values()) != 100:
            raise ValueError("标准草稿维度权重必须非负且合计为 100")
        from core.evaluation.models import EvalStandard

        for row in self.db.execute(
            select(EvalStandard).where(
                EvalStandard.domain_id == draft.domain_id,
                EvalStandard.object_type == draft.object_type,
                EvalStandard.status == "active",
            )
        ).scalars().all():
            row.status = "archived"
        draft_row = self.db.get(EvalStandard, draft_id)
        draft_row.status = "active"
        draft_row.reviewed_by = reviewer
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else reviewer,
                action="growth.standard.publish",
                resource=draft_id,
                detail={"reviewer": reviewer},
            )
        )
        self.db.commit()
        return {"standard_id": draft_id, "status": "active"}

    def list_standards(self, domain_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "standard_id": s.standard_id,
                "domain_id": s.domain_id,
                "object_type": s.object_type,
                "version": s.version,
                "status": s.status,
                "sample_size": s.sample_size,
                "reviewed_by": s.reviewed_by,
            }
            for s in eval_repo(self.db).list_standards(domain_id)
        ]

    # ---------------- 标注数据导出（P2 预留） ----------------
    def export_dataset(self, domain_id: str, actor: Actor | None) -> dict[str, Any]:
        rows = (
            self.db.execute(
                select(GrowthFeedback).where(GrowthFeedback.domain_id == domain_id).order_by(GrowthFeedback.created_at.asc())
            )
            .scalars()
            .all()
        )
        samples = []
        for row in rows:
            samples.append(
                {
                    "feedback_id": row.feedback_id,
                    "session_id": row.session_id,
                    "message_id": row.message_id,
                    "eval_id": row.eval_id,
                    "rating": row.rating,
                    "sample_type": row.sample_type,
                    "correction": row.correction,
                    "comment": row.comment,
                    "trace_id": row.trace_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action="growth.dataset.export",
                resource=domain_id,
                detail={"count": len(samples)},
            )
        )
        self.db.commit()
        return {"domain_id": domain_id, "count": len(samples), "format": "jsonl-ready", "samples": samples}
