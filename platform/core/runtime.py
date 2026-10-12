"""运行时装配根（composition root）。

进程级单例：模型网关、领域插件注册表、工具注册表；
请求级构件：仓储/服务/引擎均以 db Session 构造（FastAPI `Depends(get_db)` 注入）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from adapters.blob_local import LocalBlobRepository
from adapters.model_gateway import build_model_gateway
from adapters.repos_sql import SqlAuditRepository, SqlEvalRepository, SqlMemoryRepository
from adapters.storage_sqlite import SqliteKnowledgeRepository
from app.config import get_settings

_singletons: dict[str, Any] = {}


def get_model_gateway():
    if "model_gateway" not in _singletons:
        _singletons["model_gateway"] = build_model_gateway(get_settings())
    return _singletons["model_gateway"]


def get_blob_repo() -> LocalBlobRepository:
    if "blob_repo" not in _singletons:
        _singletons["blob_repo"] = LocalBlobRepository(Path(get_settings().DATA_DIR) / "blobs")
    return _singletons["blob_repo"]


def get_domain_registry():
    if "domain_registry" not in _singletons:
        import importlib

        from core.domains import DomainRegistry  # 延迟导入避免环

        registry = DomainRegistry.load_default()
        # 挂载领域工具集（@tool 装饰器进程内注册）
        for plugin in registry.all():
            try:
                importlib.import_module(f"domains.{plugin.domain_id}.toolset")
            except ImportError:
                continue
        _singletons["domain_registry"] = registry
    return _singletons["domain_registry"]


# ---------------- 请求级仓储工厂 ----------------
def knowledge_repo(db: Session):
    settings = get_settings()
    if settings.DATABASE_URL.startswith("postgresql"):  # pragma: no cover - 依赖外部 PG
        from adapters.storage_pgvector import PgVectorKnowledgeRepository

        return PgVectorKnowledgeRepository(db)
    return SqliteKnowledgeRepository(db)


def memory_repo(db: Session) -> SqlMemoryRepository:
    return SqlMemoryRepository(db)


def eval_repo(db: Session) -> SqlEvalRepository:
    return SqlEvalRepository(db)


def audit_repo(db: Session) -> SqlAuditRepository:
    return SqlAuditRepository(db)


def reset_runtime() -> None:
    """测试用：清空进程级单例。"""
    _singletons.clear()
