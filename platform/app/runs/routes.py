"""运行查看（05-A）：状态、事件安全投影、inspect 时间线。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import assert_domain_access, get_current_actor, require_permissions
from core.abstraction.dtos import Actor
from core.runs.approvals import (
    ApprovalConflict,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalService,
)
from core.runs.fork import ForkInputRejected, ForkUnavailable, LiveEvalForkService
from core.runs.output_reviews import (
    OutputReviewConflict,
    OutputReviewNotFound,
    OutputReviewService,
)
from core.runs.replay import FrozenReplayService, ReplayUnavailable, ReplayVersionMismatch
from core.runs.service import RunAccessDenied, RunConflict, RunService

router = APIRouter(tags=["runs"])


def _not_found() -> Exception:
    return biz("RUN_NOT_FOUND", "运行不存在或无权查看", 404)


@router.get("/runs/{run_id}", summary="运行状态与版本")
def get_run(
    run_id: str,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(RunService(db).get_run_view(run_id, actor))
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc


@router.get("/runs/{run_id}/events", summary="事件安全投影（seq 游标分页，不含原始载荷）")
def list_events(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        events = RunService(db).list_events(run_id, actor, after_seq=after_seq, limit=limit)
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc
    next_cursor = events[-1]["seq"] if events else after_seq
    return ok({"items": events, "next_cursor": next_cursor, "page_size": limit})


class ApprovalDecisionIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=500)


def _approval_view(approval) -> dict:
    return {
        "approval_id": approval.approval_id,
        "run_id": approval.run_id,
        "session_id": approval.session_id,
        "tool_name": approval.tool_name,
        "tool_version": approval.tool_version,
        "effect_key": approval.effect_key,
        "status": approval.status,
        "preview": approval.preview,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "decided_by": approval.decided_by,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
    }


@router.get("/tool-approvals", summary="待审批危险工具（域内）")
def list_approvals(
    domain_id: str = "chemical",
    actor: Actor = Depends(require_permissions("tool.approve")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, domain_id, db=db, require_online=False)
    rows = ApprovalService(db).list_pending(domain_id)
    return ok([_approval_view(row) for row in rows])


@router.get("/tool-approvals/{approval_id}", summary="审批详情（脱敏预览）")
def get_approval(
    approval_id: str,
    actor: Actor = Depends(require_permissions("tool.approve")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(_approval_view(ApprovalService(db).view(approval_id, actor)))
    except ApprovalNotFound as exc:
        raise biz("APPROVAL_NOT_FOUND", "审批不存在", 404) from exc


@router.post("/tool-approvals/{approval_id}/decide", summary="批准/拒绝（一次性，绑定 effect 与参数）")
def decide_approval(
    approval_id: str,
    body: ApprovalDecisionIn,
    actor: Actor = Depends(require_permissions("tool.approve")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        approval = ApprovalService(db).decide(approval_id, body.action, actor)
    except ApprovalNotFound as exc:
        raise biz("APPROVAL_NOT_FOUND", "审批不存在", 404) from exc
    except ApprovalExpired as exc:
        raise biz("APPROVAL_EXPIRED", "审批已过期", 410) from exc
    except ApprovalConflict as exc:
        raise biz("APPROVAL_CONFLICT", str(exc), 409) from exc
    return ok(_approval_view(approval))


class ReplayIn(BaseModel):
    mode: str = Field(default="frozen", pattern="^frozen$")


class ForkIn(BaseModel):
    mode: str = Field(default="live_eval", pattern="^live_eval$")
    user_msg: str = Field(min_length=1, max_length=4000)
    checkpoint_id: str | None = Field(default=None, max_length=64)


@router.post("/runs/{run_id}/forks", summary="live_eval 分叉（只读工具、零业务写入）")
def fork_run(
    run_id: str,
    body: ForkIn,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        report = asyncio.run(
            LiveEvalForkService(db).fork(
                run_id, actor, user_msg=body.user_msg, checkpoint_id=body.checkpoint_id
            )
        )
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc
    except ForkUnavailable as exc:
        raise biz("FORK_UNAVAILABLE", str(exc), 409) from exc
    except ForkInputRejected as exc:
        raise biz(exc.code, str(exc), 400) from exc
    except ValueError as exc:
        raise biz("VALIDATION_ERROR", str(exc), 400) from exc
    return ok(report)


@router.post("/runs/{run_id}/replays", summary="frozen 回放（同步纯计算，零业务写入）")
def replay_run(
    run_id: str,
    body: ReplayIn,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        report = asyncio.run(FrozenReplayService(db).replay(run_id, actor))
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc
    except ReplayVersionMismatch as exc:
        raise biz("REPLAY_VERSION_MISMATCH", str(exc), 409) from exc
    except ReplayUnavailable as exc:
        raise biz("REPLAY_UNAVAILABLE", str(exc), 409) from exc
    return ok(report)


@router.post("/runs/{run_id}/cancel", summary="取消运行（已发生的外部动作不回滚）")
def cancel_run(
    run_id: str,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        run = RunService(db).cancel(run_id, actor)
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc
    except RunConflict as exc:
        raise biz("STATE_INVALID", str(exc), 409) from exc
    return ok({"run_id": run.run_id, "status": run.status, "status_version": run.status_version})


@router.get("/runs/{run_id}/diff", summary="双运行安全摘要 diff（双重授权）")
def diff_runs(
    run_id: str,
    against: str = Query(min_length=1, max_length=64),
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(RunService(db).diff(run_id, against, actor))
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc


def _parse_last_event_id(value: str | None, run_id: str) -> int:
    if not value:
        return 0
    raw = value.strip()
    if ":" in raw:
        prefix, seq_text = raw.rsplit(":", 1)
        if prefix != run_id:
            raise biz("VALIDATION_ERROR", "Last-Event-ID 与运行不匹配", 400)
        raw = seq_text
    try:
        seq = int(raw)
    except ValueError as exc:
        raise biz("VALIDATION_ERROR", "Last-Event-ID 非法", 400) from exc
    if seq < 0:
        raise biz("VALIDATION_ERROR", "Last-Event-ID 非法", 400)
    return seq


@router.get("/runs/{run_id}/stream", summary="事件补帧（Last-Event-ID，安全投影）")
def stream_run(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    service = RunService(db)
    try:
        service.get_run_view(run_id, actor)
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc
    start_seq = _parse_last_event_id(last_event_id, run_id)

    async def event_stream():
        cursor = start_seq
        while True:
            page = service.list_events(run_id, actor, after_seq=cursor, limit=100)
            if not page:
                break
            for event in page:
                yield f"id: {run_id}:{event['seq']}\nevent: run.event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                cursor = event["seq"]
            service._check_view(service.get_run(run_id), actor)  # 每批重查权限
        yield f"event: replay_end\ndata: {json.dumps({'last_seq': cursor}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/output-reviews", summary="待输出审核（域内）")
def list_output_reviews(
    domain_id: str = "chemical",
    actor: Actor = Depends(require_permissions("output.review")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, domain_id, db=db, require_online=False)
    rows = OutputReviewService(db).list_pending(domain_id)
    return ok([OutputReviewService.review_view(row) for row in rows])


@router.get("/output-reviews/{review_id}", summary="输出审核详情")
def get_output_review(
    review_id: str,
    actor: Actor = Depends(require_permissions("output.review")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(OutputReviewService.review_view(OutputReviewService(db).view(review_id, actor)))
    except OutputReviewNotFound as exc:
        raise biz("OUTPUT_REVIEW_NOT_FOUND", "输出审核不存在", 404) from exc


@router.post("/output-reviews/{review_id}/decide", summary="批准/拒绝输出")
def decide_output_review(
    review_id: str,
    body: ApprovalDecisionIn,
    actor: Actor = Depends(require_permissions("output.review")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        review = OutputReviewService(db).decide(review_id, body.action, actor)
    except OutputReviewNotFound as exc:
        raise biz("OUTPUT_REVIEW_NOT_FOUND", "输出审核不存在", 404) from exc
    except OutputReviewConflict as exc:
        raise biz("OUTPUT_REVIEW_CONFLICT", str(exc), 409) from exc
    return ok(OutputReviewService.review_view(review))


@router.get("/runs/{run_id}/inspect", summary="inspect：纯事件 reducer 时间线 + 哈希链校验")
def inspect_run(
    run_id: str,
    actor: Actor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(RunService(db).inspect(run_id, actor))
    except (KeyError, RunAccessDenied) as exc:
        raise _not_found() from exc
