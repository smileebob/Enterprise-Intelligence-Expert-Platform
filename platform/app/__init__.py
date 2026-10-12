"""企业级领域专家 Agent 平台 · 应用工厂（规则 A-01）。"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.middleware import SlowAPIMiddleware

from app.common.errors import register_exception_handlers
from app.common.logging_setup import setup_logging
from app.common.middleware import SecurityHeadersMiddleware, TraceMiddleware
from app.common.rate_limit import limiter
from app.config import get_settings

logger = logging.getLogger(__name__)


def _init_cache(settings) -> None:
    """缓存后端集中初始化（规则 E-01：fastapi-cache2；prefix 统一 APP_CODE）。"""
    from core.cache import init_cache

    init_cache(settings)


def create_app(config_name: str | None = None) -> FastAPI:
    settings = get_settings(config_name)
    setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description=(
            "企业级领域专家 Agent 平台（赛事演示实现）：知识管道 KP-1 + 智能体编排 + 六维评估 "
            "+ 四层记忆 + 工具权限网关 + 安全 Hook。全部演示数据为赛事模拟数据。"
        ),
        debug=settings.DEBUG,
    )
    app.state.settings = settings

    # 中间件（后添加者在最外层）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TraceMiddleware)

    app.state.limiter = limiter
    register_exception_handlers(app)

    _init_cache(settings)

    # 运维端点（免认证免限流）
    from app.ops.routes import router as ops_router

    app.include_router(ops_router)

    # 业务路由（统一 /api/v1 前缀，规则 B-03）
    from app.auth.routes import router as auth_router
    from app.chat.routes import router as chat_router
    from app.domains.routes import router as domains_router
    from app.evaluation.routes import router as evaluation_router
    from app.governance.routes import router as governance_router
    from app.growth.routes import router as growth_router
    from app.knowledge.routes import router as knowledge_router
    from app.memory.routes import router as memory_router

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(evaluation_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    app.include_router(domains_router, prefix="/api/v1")
    app.include_router(governance_router, prefix="/api/v1")
    app.include_router(growth_router, prefix="/api/v1")

    if settings.METRICS_ENABLED:
        try:
            from prometheus_fastapi_instrumentator import Instrumentator

            Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
        except Exception:  # pragma: no cover - 指标注册失败不阻断应用
            logger.warning("prometheus instrumentator init failed", exc_info=True)

    return app
