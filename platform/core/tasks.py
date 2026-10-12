"""Celery 任务：知识入库解析（幂等可重试）、记忆衰减/压缩（BR-07 周期任务）。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from core.celery_app import celery_app
from core.clock import utc_now

logger = logging.getLogger(__name__)


def _ingest_file(task_id: str) -> Path:
    return Path(get_settings().DATA_DIR) / "ingest" / f"{task_id}.json"


@celery_app.task(name="core.tasks.ingest_document", bind=True, max_retries=2, default_retry_delay=5)
def ingest_document(self, task_id: str) -> str:  # pragma: no cover - 由路由/测试驱动
    """入库任务：读取暂存载荷 → KP-1 管道 →（必要时）冲突对象 → 状态流转。"""
    from core.knowledge.ingest import get_ingest_adapter
    from core.knowledge.models import KbIngestTask
    from core.knowledge.service import KnowledgeService

    db = SessionLocal()
    path = _ingest_file(task_id)
    try:
        task = db.get(KbIngestTask, task_id)
        if task is None:
            return "missing"
        if not path.exists():
            task.status = "failed"
            task.error = "入库载荷缺失（临时文件已清理或未写入）"
            db.commit()
            return "failed"

        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        service = KnowledgeService(db)

        if task.source_name == "conflict_scenarios.json":
            adapter = get_ingest_adapter(task.domain_id)
            builder = getattr(adapter, "build_conflicts_from_scenarios", None)
            conflicts = builder(payload) if builder else []
            service.repo.upsert_conflicts(conflicts)
            db.commit()
            task.status = "ready"
            task.docs = 0
            task.chunks = 0
            task.conflicts_detected = len(conflicts)
        else:
            package = asyncio.run(
                service.ingest(
                    task.domain_id,
                    task.source_name,
                    payload,
                    source_type=envelope.get("source_type"),
                    permission_level=envelope.get("permission_level"),
                )
            )
            service.write_structured(task.domain_id, payload, task.source_name)
            task.status = "ready"
            task.docs = package.stats.get("docs", 0)
            task.chunks = package.stats.get("chunks", 0)
            task.conflicts_detected = package.stats.get("conflicts_detected", 0)

        task.pending_review = task.conflicts_detected > 0
        task.finished_at = utc_now()
        db.commit()
        logger.info("ingest task %s finished: %s", task_id, task.status)
        return task.status
    except Exception as exc:
        db.rollback()
        task = db.get(KbIngestTask, task_id)
        if task is not None:
            task.status = "failed"
            task.error = str(exc)[:500]
            task.finished_at = utc_now()
            db.commit()
        logger.exception("ingest task %s failed", task_id)
        raise
    finally:
        db.close()
        path.unlink(missing_ok=True)


@celery_app.task(name="core.tasks.memory_maintenance")
def memory_maintenance() -> dict:  # pragma: no cover - 周期任务
    """记忆维护：衰减（BR-07）+ 压缩（模式记忆）。"""
    from core.memory.service import MemoryService

    db = SessionLocal()
    try:
        service = MemoryService(db)
        stats = service.decay()
        patterns = service.compact("chemical", min_group=3)
        return {"decay": stats, "patterns": len(patterns)}
    finally:
        db.close()
