"""运维端点：/healthz（存活）、/readyz（就绪，含 DB/缓存探测）。免认证免限流（规则 J-04）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.rate_limit import limiter
from app.db import get_db
from core.cache import cache_ping

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


@router.get("/healthz")
@limiter.exempt
def healthz() -> dict:
    """存活探针。"""
    return {"status": "UP"}


@router.get("/readyz")
@limiter.exempt
async def readyz(db: Session = Depends(get_db)):
    """就绪探针：检查数据库与缓存依赖。"""
    checks: dict[str, str] = {}
    healthy = True
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.warning("readyz database check failed", exc_info=True)
        checks["database"] = "fail"
        healthy = False
    checks["cache"] = "ok" if await cache_ping() else "fail"
    if not healthy:
        return JSONResponse(status_code=503, content={"status": "NOT_READY", "checks": checks})
    return {"status": "READY", "checks": checks}
