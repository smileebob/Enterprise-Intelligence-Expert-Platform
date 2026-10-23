"""运行时装配根（composition root）。

进程级单例：模型网关、领域插件注册表、工具注册表；
请求级构件：仓储/服务/引擎均以 db Session 构造（FastAPI `Depends(get_db)` 注入）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
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


class DbDomainRegistry:
    """以 DB 生效 revision 为权威的领域解析器（02 §5）。

    每次准入读取实例指针（轻量查询）；同一 (config_rev, revision, status) 复用解析结果，
    另一进程发布后下一回合自然读到新配置。文件注册表仅负责工具集注册与代码资产。
    """

    def __init__(self, db: Session):
        self.db = db
        self._cache: dict[str, tuple[tuple[Any, ...], Any]] = {}

    def get(self, domain_id: str):
        from core.admin.models import DomInstance, DomRevision
        from core.domains import DomainPlugin, load_domain_hooks

        row = self.db.get(DomInstance, domain_id)
        if row is None:
            return None
        revision_id = row.active_revision_id or row.draft_revision_id
        revision = self.db.get(DomRevision, revision_id) if revision_id else None
        config = dict(revision.config) if revision is not None else dict(row.config or {})
        code_version = revision.code_version if revision is not None else ""
        key = (row.config_rev, revision_id or "", row.status, code_version)
        cached = self._cache.get(domain_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        plugin = DomainPlugin(
            domain_id=row.domain_id,
            name=row.name,
            version=row.version,
            status=row.status,
            base_dir=Path("domains") / row.domain_id,
            knowledge_binding=config.get("knowledge_binding", {}),
            eval_config=config.get("eval_config", {}),
            agent_config=config.get("agent_config", {}),
            toolset_ref=config.get("toolset_ref", ""),
            memory_policy=config.get("memory_policy", {}),
            hooks=load_domain_hooks(row.domain_id),
        )
        self._cache[domain_id] = (key, plugin)
        return plugin

    def require(self, domain_id: str):
        plugin = self.get(domain_id)
        if plugin is None:
            raise KeyError(f"领域实例未装配: {domain_id}")
        return plugin

    def all(self) -> list[Any]:
        from core.admin.models import DomInstance

        rows = self.db.execute(select(DomInstance.domain_id)).scalars().all()
        return [plugin for plugin in (self.get(domain_id) for domain_id in rows) if plugin is not None]

    def online(self) -> list[Any]:
        return [plugin for plugin in self.all() if plugin.status == "online"]


def get_db_domain_registry(db: Session) -> DbDomainRegistry:
    return DbDomainRegistry(db)


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
