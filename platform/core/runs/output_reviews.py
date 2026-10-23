"""输出审核（06-C）：审核前不发正文；批准绑定草稿摘要，改稿失效。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.orchestration.state import Draft
from core.runs.models import OutputReview
from core.runs.service import canonical_json


class OutputReviewNotFound(Exception):
    pass


class OutputReviewConflict(Exception):
    pass


def draft_digest(draft: Draft | None) -> str:
    if draft is None:
        return ""
    payload = {
        "summary": draft.summary,
        "sections": [section.to_dict() for section in draft.sections],
        "citations": draft.citations,
        "annotations": draft.annotations,
        "confidence": draft.confidence,
        "degrade_level": draft.degrade_level,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()  # noqa: S324


class OutputReviewService:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        run_id: str,
        session_id: str,
        owner_id: str,
        domain_id: str,
        draft: Draft,
        action: str,
        reasons: list[str],
        policy_version: str,
    ) -> OutputReview:
        review = OutputReview(
            review_id=f"ORV-{uuid.uuid4().hex[:16]}",
            run_id=run_id,
            session_id=session_id,
            owner_id=owner_id,
            domain_id=domain_id,
            draft_digest=draft_digest(draft),
            policy_version=policy_version,
            action_requested=action,
            reasons=list(reasons),
            status="pending",
        )
        self.db.add(review)
        self.db.commit()
        return review

    def decide(self, review_id: str, action: str, actor) -> OutputReview:
        review = self.db.get(OutputReview, review_id)
        if review is None or review.domain_id not in (actor.domain_scope or []):
            raise OutputReviewNotFound("审核不存在")
        if review.status != "pending":
            raise OutputReviewConflict(f"审核状态为 {review.status}，不可再次决策")
        new_status = "approved" if action == "approve" else "rejected"
        result = self.db.execute(
            update(OutputReview)
            .where(OutputReview.review_id == review_id, OutputReview.status == "pending")
            .values(status=new_status, reviewer=actor.user_id, decided_at=datetime.now(UTC))
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            raise OutputReviewConflict("审核已被其他操作处理")
        self.db.commit()
        self.db.refresh(review)
        return review

    def consume(self, review_id: str, expected_digest: str, actor) -> OutputReview:
        review = self.db.get(OutputReview, review_id)
        if review is None or review.domain_id not in (actor.domain_scope or []):
            raise OutputReviewNotFound("审核不存在")
        if review.status != "approved":
            raise OutputReviewConflict(f"审核状态为 {review.status}，不能放行输出")
        if review.draft_digest != expected_digest:
            raise OutputReviewConflict("草稿已变更，原审核批准失效")
        if actor.user_id not in (review.owner_id, review.reviewer):
            raise OutputReviewNotFound("审核不存在")
        result = self.db.execute(
            update(OutputReview)
            .where(OutputReview.review_id == review_id, OutputReview.status == "approved")
            .values(status="consumed")
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            raise OutputReviewConflict("审核已被消费")
        self.db.commit()
        self.db.refresh(review)
        return review

    def view(self, review_id: str, actor) -> OutputReview:
        review = self.db.get(OutputReview, review_id)
        if review is None or review.domain_id not in (actor.domain_scope or []):
            raise OutputReviewNotFound("审核不存在")
        return review

    def list_pending(self, domain_id: str, limit: int = 50) -> list[OutputReview]:
        rows = (
            self.db.execute(
                select(OutputReview)
                .where(OutputReview.domain_id == domain_id, OutputReview.status == "pending")
                .order_by(OutputReview.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows)

    @staticmethod
    def review_view(review: OutputReview) -> dict[str, Any]:
        return {
            "review_id": review.review_id,
            "run_id": review.run_id,
            "session_id": review.session_id,
            "domain_id": review.domain_id,
            "draft_digest": review.draft_digest,
            "policy_version": review.policy_version,
            "action_requested": review.action_requested,
            "reasons": review.reasons,
            "status": review.status,
            "reviewer": review.reviewer,
            "decided_at": review.decided_at.isoformat() if review.decided_at else None,
        }
