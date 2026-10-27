"""多轮政策解读会话：保存澄清状态、恢复条件、结果与幂等操作。"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.common.errors import BusinessError
from app.gov_office.schemas import (
    InterpretSessionCreateIn,
    InterpretSessionTurnIn,
    PolicyInterpretIn,
)
from app.gov_office.services import GovPolicyService
from core.abstraction.dtos import Actor
from core.abstraction.enums import HookStage
from core.guardrails.hooks import HookContext
from core.guardrails.input_hook import InputGuard
from domains.gov_office.models import GovInterpretOperation, GovInterpretSession

DOMAIN_ID = "gov_office"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_hash(payload: Any) -> str:
    raw = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    encoded = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256(encoded)


def _session_view(row: GovInterpretSession, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "owner_id": row.owner_id,
        "question": row.question,
        "jurisdiction": row.jurisdiction,
        "subject": row.subject,
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "status": row.status,
        "turns": row.turns or [],
        "result": result if result is not None else row.last_result,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class GovInterpretConversationService:
    def __init__(self, db: Session):
        self.db = db

    async def create(
        self,
        payload: InterpretSessionCreateIn,
        actor: Actor,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_hash = _request_hash(payload)
        operation = self._operation(idempotency_key)
        if operation is not None:
            if operation.request_hash != request_hash:
                raise BusinessError(
                    "IDEMPOTENCY_CONFLICT", "幂等键对应不同会话请求", 409
                )
            return {**operation.response, "duplicated": True}

        question = self._guard(payload.question, actor, "")
        session = GovInterpretSession(
            session_id=f"GIS-{uuid.uuid4().hex[:20]}",
            owner_id=actor.user_id,
            question=question,
            jurisdiction=payload.jurisdiction or "",
            subject=payload.subject,
            as_of=payload.as_of,
            status="clarify",
            turns=[],
        )
        self.db.add(session)
        self.db.flush()
        result = await self._evaluate(session, actor)
        self._append_turns(session, question, result)
        response = {
            "session": _session_view(session, result),
            "result": result,
        }
        self.db.add(
            GovInterpretOperation(
                operation_id=f"GIO-{uuid.uuid4().hex[:20]}",
                session_id=session.session_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
        )
        self.db.commit()
        return {**response, "duplicated": False}

    async def turn(
        self,
        session_id: str,
        payload: InterpretSessionTurnIn,
        actor: Actor,
        idempotency_key: str,
    ) -> dict[str, Any]:
        session = self._owned_session(session_id, actor)
        request_hash = _request_hash(payload)
        operation = self._operation(idempotency_key)
        if operation is not None:
            if operation.session_id != session_id or operation.request_hash != request_hash:
                raise BusinessError(
                    "IDEMPOTENCY_CONFLICT", "幂等键对应不同会话请求", 409
                )
            return {**operation.response, "duplicated": True}

        if payload.question is not None:
            session.question = self._guard(payload.question, actor, session_id)
        if payload.jurisdiction is not None:
            session.jurisdiction = payload.jurisdiction
        if payload.subject is not None:
            session.subject = payload.subject
        if payload.as_of is not None:
            session.as_of = payload.as_of
        user_text = session.question
        if payload.jurisdiction is not None:
            user_text += f"\n政策地区：{payload.jurisdiction}"
        if payload.as_of is not None:
            user_text += f"\n事项时间：{payload.as_of.isoformat()}"
        if payload.subject is not None:
            user_text += f"\n政策对象：{payload.subject}"
        result = await self._evaluate(session, actor)
        self._append_turns(session, user_text, result)
        response = {
            "session": _session_view(session, result),
            "result": result,
        }
        self.db.add(
            GovInterpretOperation(
                operation_id=f"GIO-{uuid.uuid4().hex[:20]}",
                session_id=session_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
        )
        self.db.commit()
        return {**response, "duplicated": False}

    def get(self, session_id: str, actor: Actor) -> dict[str, Any]:
        return _session_view(self._owned_session(session_id, actor))

    async def _evaluate(
        self, session: GovInterpretSession, actor: Actor
    ) -> dict[str, Any]:
        result = await GovPolicyService(self.db).interpret(
            PolicyInterpretIn(
                question=session.question,
                jurisdiction=session.jurisdiction or None,
                subject=session.subject,
                as_of=session.as_of,
                top_k=6,
            ),
            actor.permission_max,
        )
        session.status = "clarify" if result["outcome"] == "clarify" else "answered"
        session.last_result = result
        return result

    def _append_turns(
        self, session: GovInterpretSession, user_text: str, result: dict[str, Any]
    ) -> None:
        if result["outcome"] == "clarify":
            assistant_text = "；".join(
                item["question"] for item in result.get("clarifications", [])
            )
        elif result["outcome"] == "insufficient":
            assistant_text = "；".join(result.get("warnings", []))
        else:
            assistant_text = result.get("summary", "")
        turns = list(session.turns or [])
        turns.extend(
            [
                {"role": "user", "content": user_text},
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "outcome": result["outcome"],
                    "claim_ids": [
                        item["claim_id"] for item in result.get("claims", [])
                    ],
                },
            ]
        )
        session.turns = turns[-40:]

    def _guard(self, text: str, actor: Actor, session_id: str) -> str:
        ctx = HookContext(
            stage=HookStage.INPUT,
            actor=actor,
            domain_id=DOMAIN_ID,
            session_id=session_id,
        )
        decision = InputGuard(self.db).check(text, ctx)
        if decision.blocked:
            raise BusinessError(decision.code, decision.message, 400)
        return decision.payload.get("text", text)

    def _operation(self, key: str) -> GovInterpretOperation | None:
        return (
            self.db.query(GovInterpretOperation)
            .filter(GovInterpretOperation.idempotency_key == key)
            .first()
        )

    def _owned_session(
        self, session_id: str, actor: Actor
    ) -> GovInterpretSession:
        row = self.db.get(GovInterpretSession, session_id)
        if row is None or row.owner_id != actor.user_id:
            raise BusinessError("INTERPRET_SESSION_NOT_FOUND", "政策解读会话不存在", 404)
        return row
