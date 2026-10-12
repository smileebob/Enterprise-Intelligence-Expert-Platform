"""平台管理与配置（M9，技术方案 05 §5.6）：领域装配（校验/发布/回滚）、角色权限管理、审计查询。

装配五要素完整性校验（任一缺失即 VALIDATION_ERROR）；status 变更全部版本化留痕（config_history）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.abstraction.dtos import Actor, AuditEntry
from core.admin.models import DomInstance
from core.gateway.permissions import (
    PERMISSIONS,
    ensure_permission_tables,
    invalidate_permission_cache,
    role_permissions,
)
from core.runtime import audit_repo

logger = logging.getLogger(__name__)

REQUIRED_ELEMENTS = ("knowledge_binding", "eval_config", "agent_config", "memory_policy")


class AdminService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------- 领域装配（F7） ----------------
    def validate_domain_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """五要素完整性校验 + 打分器可解析（能校验的范围内）。"""
        problems: list[str] = []
        for element in REQUIRED_ELEMENTS:
            if not config.get(element):
                problems.append(f"缺少必需要素：{element}")
        if not config.get("toolset_ref") and not config.get("toolset"):
            problems.append("缺少必需要素：toolset_ref")
        if config.get("eval_config") and not config["eval_config"].get("dimensions"):
            problems.append("eval_config.dimensions 为空")
        if config.get("knowledge_binding") and not config["knowledge_binding"].get("collections"):
            problems.append("knowledge_binding.collections 为空")
        if problems:
            return {"validation": "FAIL", "problems": problems}
        return {"validation": "PASS", "problems": []}

    def submit_domain(self, config: dict[str, Any], actor: Actor | None) -> dict[str, Any]:
        check = self.validate_domain_config(config)
        if check["validation"] != "PASS":
            return {"validation": "FAIL", "problems": check["problems"]}

        domain_id = config["domain_id"]
        row = self.db.get(DomInstance, domain_id)
        version = str(config.get("version", "0.1.0"))
        if row is None:
            row = DomInstance(domain_id=domain_id, name=config.get("name", domain_id))
            self.db.add(row)
            row.config_history = []
        else:
            # 保存上一版本供回滚
            history = list(row.config_history or [])
            history.append({"version": row.version, "config": row.config, "status": row.status})
            row.config_history = history[-5:]
        row.name = config.get("name", row.name)
        row.config = config
        row.version = version
        row.status = "draft"
        row.created_by = actor.user_id if actor else None
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action="domain.submit",
                resource=domain_id,
                detail={"version": version, "validation": "PASS"},
            )
        )
        self.db.commit()
        return {"domain_id": domain_id, "status": "draft", "version": version, "validation": "PASS"}

    def publish_domain(self, domain_id: str, actor: Actor | None) -> dict[str, Any]:
        row = self.db.get(DomInstance, domain_id)
        if row is None:
            raise KeyError(f"领域实例不存在: {domain_id}")
        row.status = "online"
        self._sync_plugin_registry(row, publish=True)
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action="domain.publish",
                resource=domain_id,
                detail={"version": row.version},
            )
        )
        self.db.commit()
        return {"domain_id": domain_id, "status": "online", "version": row.version}

    def rollback_domain(self, domain_id: str, actor: Actor | None) -> dict[str, Any]:
        """回滚 = 切回上一版本 config（MVP 两态直发+版本回滚）。"""
        row = self.db.get(DomInstance, domain_id)
        if row is None:
            raise KeyError(f"领域实例不存在: {domain_id}")
        history = list(row.config_history or [])
        if not history:
            raise ValueError("没有可回滚的历史版本")
        previous = history.pop()
        row.config_history = history
        row.config = previous["config"]
        row.version = previous["version"]
        row.status = previous.get("status", "draft")
        self._sync_plugin_registry(row, publish=row.status == "online")
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action="domain.rollback",
                resource=domain_id,
                detail={"restored_version": row.version},
            )
        )
        self.db.commit()
        return {"domain_id": domain_id, "status": row.status, "version": row.version}

    def offline_domain(self, domain_id: str, actor: Actor | None) -> dict[str, Any]:
        row = self.db.get(DomInstance, domain_id)
        if row is None:
            raise KeyError(f"领域实例不存在: {domain_id}")
        row.status = "offline"
        self._sync_plugin_registry(row, publish=False)
        audit_repo(self.db).append(
            AuditEntry(trace_id="", actor_id=actor.user_id if actor else None, action="domain.offline", resource=domain_id)
        )
        self.db.commit()
        return {"domain_id": domain_id, "status": "offline", "version": row.version}

    def _sync_plugin_registry(self, row: DomInstance, *, publish: bool) -> None:
        """装配生效：把域实例配置注入进程内领域注册表（配置即领域，底座零改动）。"""
        from core.runtime import get_domain_registry

        registry = get_domain_registry()
        plugin = registry.get(row.domain_id)
        if plugin is None:
            from pathlib import Path

            from core.domains import DomainPlugin

            base = Path("domains") / row.domain_id
            registry.register(
                DomainPlugin(
                    domain_id=row.domain_id,
                    name=row.name,
                    version=row.version,
                    status=row.status if publish else "offline",
                    base_dir=base,
                    knowledge_binding=row.config.get("knowledge_binding", {}),
                    eval_config=row.config.get("eval_config", {}),
                    agent_config=row.config.get("agent_config", {}),
                    toolset_ref=row.config.get("toolset_ref", ""),
                    memory_policy=row.config.get("memory_policy", {}),
                )
            )
            return
        plugin.status = row.status if publish else "offline"
        plugin.version = row.version
        plugin.name = row.name

    def list_domains(self) -> list[dict[str, Any]]:
        rows = self.db.execute(select(DomInstance)).scalars().all()
        return [
            {
                "domain_id": r.domain_id,
                "name": r.name,
                "status": r.status,
                "version": r.version,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]

    def get_domain(self, domain_id: str) -> dict[str, Any]:
        row = self.db.get(DomInstance, domain_id)
        if row is None:
            raise KeyError(f"领域实例不存在: {domain_id}")
        return {
            "domain_id": row.domain_id,
            "name": row.name,
            "status": row.status,
            "version": row.version,
            "config": row.config,
            "config_history_count": len(row.config_history or []),
        }

    # ---------------- 角色权限（FR-9-04） ----------------
    def list_roles(self) -> list[dict[str, Any]]:
        ensure_permission_tables(self.db)
        from core.admin.models import GovRole

        roles = self.db.execute(select(GovRole)).scalars().all()
        return [
            {"role_id": r.role_id, "name": r.name, "description": r.description, "permissions": sorted(role_permissions(self.db, r.role_id))}
            for r in roles
        ]

    def list_permissions(self) -> list[dict[str, Any]]:
        ensure_permission_tables(self.db)
        return PERMISSIONS

    def update_role_permissions(self, role_id: str, permissions: list[str], actor: Actor | None) -> dict[str, Any]:
        ensure_permission_tables(self.db)
        from core.admin.models import GovRole, GovRolePermission

        if self.db.get(GovRole, role_id) is None:
            raise KeyError(f"角色不存在: {role_id}")
        known = {p["perm_id"] for p in PERMISSIONS}
        unknown = [p for p in permissions if p not in known]
        if unknown:
            raise ValueError(f"未知权限点: {'、'.join(unknown)}")
        self.db.execute(delete(GovRolePermission).where(GovRolePermission.role_id == role_id))
        for perm_id in permissions:
            self.db.add(GovRolePermission(role_id=role_id, perm_id=perm_id))
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action="role.permissions.update",
                resource=role_id,
                detail={"permissions": permissions},
            )
        )
        self.db.commit()
        invalidate_permission_cache()
        return {"role_id": role_id, "permissions": sorted(permissions)}

    # ---------------- 审计查询 ----------------
    def query_audit(self, **filters: Any) -> list[dict[str, Any]]:
        return audit_repo(self.db).query(**filters)
