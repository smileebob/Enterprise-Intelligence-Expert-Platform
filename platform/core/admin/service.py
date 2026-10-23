"""平台管理与配置（M9，技术方案 05 §5.6）：领域装配（校验/发布/回滚）、角色权限管理、审计查询。

装配五要素完整性校验（任一缺失即 VALIDATION_ERROR）；status 变更全部版本化留痕（config_history）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.abstraction.dtos import Actor, AuditEntry
from core.admin.models import DomInstance, DomOperation, DomRevision
from core.admin.revisions import content_hash, new_operation_id, new_revision_id
from core.domains import load_domain_hooks
from core.gateway.permissions import (
    PERMISSIONS,
    ensure_permission_tables,
    invalidate_permission_cache,
    role_permissions,
)
from core.runtime import audit_repo

logger = logging.getLogger(__name__)

REQUIRED_ELEMENTS = ("knowledge_binding", "eval_config", "agent_config", "memory_policy")


class PreconditionRequired(Exception):
    """缺少版本前置条件（HTTP 428）。"""


class PreconditionFailed(Exception):
    """If-Match 过期或并发 CAS 失败（HTTP 412）。"""


class IdempotencyConflict(Exception):
    """同一幂等键对应不同请求摘要（HTTP 409）。"""


class OperationInProgress(Exception):
    """同一幂等键的请求仍在处理中（HTTP 409）。"""


class AdminService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------- 领域装配（F7） ----------------
    def validate_domain_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """五要素完整性校验 + 打分器可解析（能校验的范围内）。"""
        problems: list[str] = []
        if not config.get("domain_id"):
            problems.append("缺少必需要素：domain_id")
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

    # ---------- 幂等与 CAS 基础 ----------
    def _find_operation(
        self, actor: Actor | None, operation: str, domain_id: str, idem_key: str
    ) -> DomOperation | None:
        actor_id = actor.user_id if actor else ""
        return self.db.execute(
            select(DomOperation).where(
                DomOperation.actor_id == actor_id,
                DomOperation.operation == operation,
                DomOperation.domain_id == domain_id,
                DomOperation.idem_key == idem_key,
            )
        ).scalars().first()

    def _start_operation(
        self,
        actor: Actor | None,
        operation: str,
        domain_id: str,
        idem_key: str,
        request_hash: str,
    ) -> tuple[DomOperation | None, dict[str, Any] | None]:
        """领取幂等记录；已完成的同摘要请求直接返回既有结果（优先于 If-Match 判定）。"""
        if not idem_key:
            return None, None
        existing = self._find_operation(actor, operation, domain_id, idem_key)
        if existing is not None:
            return None, self._replay(existing, request_hash)
        op = DomOperation(
            operation_id=new_operation_id(),
            actor_id=actor.user_id if actor else "",
            operation=operation,
            domain_id=domain_id,
            idem_key=idem_key,
            request_hash=request_hash,
            status="in_progress",
        )
        self.db.add(op)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            existing = self._find_operation(actor, operation, domain_id, idem_key)
            if existing is None:
                raise
            return None, self._replay(existing, request_hash)
        return op, None

    def _replay(self, existing: DomOperation, request_hash: str) -> dict[str, Any]:
        if existing.request_hash != request_hash:
            raise IdempotencyConflict("相同幂等键对应不同请求内容")
        if existing.status != "completed":
            raise OperationInProgress("相同幂等键的请求正在处理中")
        return dict(existing.response or {})

    def _commit_operation(
        self,
        op: DomOperation | None,
        result: dict[str, Any],
        *,
        action: str,
        resource: str,
        actor: Actor | None,
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        if op is not None:
            op.status = "completed"
            op.response = result
        audit_repo(self.db).append(
            AuditEntry(
                trace_id="",
                actor_id=actor.user_id if actor else None,
                action=action,
                resource=resource,
                detail=detail,
            )
        )
        self.db.commit()
        return result

    def _cas_update(self, domain_id: str, expected: int, **values: Any) -> None:
        """原子比较交换：WHERE config_rev=expected，受影响行数必须为 1。"""
        result = self.db.execute(
            update(DomInstance)
            .where(DomInstance.domain_id == domain_id, DomInstance.config_rev == expected)
            .values(config_rev=expected + 1, **values)
        )
        if getattr(result, "rowcount", 0) != 1:
            raise PreconditionFailed("配置已被其他操作更新，请刷新后重试")

    def _create_revision(
        self,
        domain_id: str,
        config_rev: int,
        config: dict[str, Any],
        *,
        kind: str,
        actor: Actor | None,
        restored_from: str | None = None,
    ) -> DomRevision:
        rev = DomRevision(
            revision_id=new_revision_id(),
            domain_id=domain_id,
            config_rev=config_rev,
            config=config,
            code_version=self._current_code_version(domain_id),
            content_hash=content_hash(config),
            change_kind=kind,
            restored_from=restored_from,
            created_by=actor.user_id if actor else None,
        )
        self.db.add(rev)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise PreconditionFailed("配置修订版本冲突，请刷新后重试") from exc
        return rev

    @staticmethod
    def _current_code_version(domain_id: str) -> str:
        try:
            hooks = load_domain_hooks(domain_id)
        except Exception:  # noqa: BLE001 - 代码版本仅用于发布校验，缺失不阻断读取
            return ""
        return getattr(hooks, "CODE_VERSION", "") if hooks is not None else ""

    def _revision_of(self, domain_id: str, revision_id: str) -> DomRevision:
        rev = self.db.get(DomRevision, revision_id)
        if rev is None or rev.domain_id != domain_id:
            raise KeyError(f"配置修订不存在: {revision_id}")
        return rev

    def _active_config(self, row: DomInstance) -> dict[str, Any]:
        if row.active_revision_id:
            rev = self.db.get(DomRevision, row.active_revision_id)
            if rev is not None:
                return dict(rev.config)
        if row.draft_revision_id:
            rev = self.db.get(DomRevision, row.draft_revision_id)
            if rev is not None:
                return dict(rev.config)
        return dict(row.config or {})

    # ---------------- 领域装配（F7 · revision + CAS） ----------------
    def submit_domain(
        self,
        config: dict[str, Any],
        actor: Actor | None,
        *,
        expected_rev: int | None = None,
        idem_key: str = "",
        request_hash: str = "",
    ) -> dict[str, Any]:
        """提交草稿：生成新 revision 并推进 draft 指针，不替换在线 active 配置。"""
        check = self.validate_domain_config(config)
        if check["validation"] != "PASS":
            return {"validation": "FAIL", "problems": check["problems"]}

        domain_id = str(config["domain_id"])
        digest = request_hash or content_hash({"op": "submit", "config": config})
        op, replay = self._start_operation(actor, "submit", domain_id, idem_key, digest)
        if replay is not None:
            return replay
        try:
            row = self.db.get(DomInstance, domain_id)
            if row is None:
                row = DomInstance(
                    domain_id=domain_id,
                    name=config.get("name", domain_id),
                    status="draft",
                    config={},
                    version="0.1.0",
                    config_rev=0,
                    created_by=actor.user_id if actor else None,
                )
                self.db.add(row)
                self.db.flush()
                expected = 0
            else:
                expected = row.config_rev if expected_rev is None else int(expected_rev)
                if row.config_rev != expected:
                    raise PreconditionFailed("配置已被其他操作更新，请刷新后重试")

            new_rev_num = expected + 1
            revision = self._create_revision(
                domain_id, new_rev_num, config, kind="draft", actor=actor
            )
            values: dict[str, Any] = {"draft_revision_id": revision.revision_id}
            if row.active_revision_id is None:
                # 从未上线（或仅草稿）：同步实例快照便于展示/后续发布
                values.update(
                    {
                        "config": config,
                        "version": str(config.get("version", row.version)),
                        "name": config.get("name", row.name),
                    }
                )
            self._cas_update(domain_id, expected, **values)
            self.db.expire(row)
            self.db.refresh(row)
            result = {
                "domain_id": domain_id,
                "status": row.status,
                "version": row.version,
                "config_rev": new_rev_num,
                "draft_revision_id": revision.revision_id,
                "validation": "PASS",
            }
            return self._commit_operation(
                op,
                result,
                action="domain.submit",
                resource=domain_id,
                actor=actor,
                detail={"config_rev": new_rev_num, "revision_id": revision.revision_id},
            )
        except Exception:
            self.db.rollback()
            raise

    def publish_domain(
        self,
        domain_id: str,
        actor: Actor | None,
        *,
        expected_rev: int | None = None,
        revision_id: str | None = None,
        idem_key: str = "",
        request_hash: str = "",
    ) -> dict[str, Any]:
        digest = request_hash or content_hash({"op": "publish", "revision_id": revision_id or ""})
        op, replay = self._start_operation(actor, "publish", domain_id, idem_key, digest)
        if replay is not None:
            return replay
        try:
            row = self._require_instance(domain_id)
            expected = row.config_rev if expected_rev is None else int(expected_rev)
            if row.config_rev != expected:
                raise PreconditionFailed("配置已被其他操作更新，请刷新后重试")
            if row.status == "online" and not revision_id and not row.draft_revision_id:
                raise ValueError("领域已在线且没有待发布草稿")
            target_id = revision_id or row.draft_revision_id or row.active_revision_id
            if not target_id:
                raise ValueError("没有可发布的草稿或在线配置")
            target = self._revision_of(domain_id, target_id)
            check = self.validate_domain_config(dict(target.config))
            if check["validation"] != "PASS":
                raise ValueError("装配校验失败：" + "；".join(check["problems"]))
            current_code = self._current_code_version(domain_id)
            if target.code_version and current_code and target.code_version != current_code:
                raise ValueError("本机已安装代码版本与修订不一致，禁止发布")
            new_rev_num = expected + 1
            revision = self._create_revision(
                domain_id,
                new_rev_num,
                dict(target.config),
                kind="publish",
                actor=actor,
                restored_from=target.revision_id,
            )
            self._cas_update(
                domain_id,
                expected,
                status="online",
                active_revision_id=revision.revision_id,
                draft_revision_id=None,
                config=dict(target.config),
                version=str(target.config.get("version", row.version)),
                name=str(target.config.get("name", row.name)),
            )
            self.db.expire(row)
            self.db.refresh(row)
            self._sync_plugin_registry(row, publish=True)
            result = {
                "domain_id": domain_id,
                "status": "online",
                "version": row.version,
                "config_rev": new_rev_num,
                "active_revision_id": revision.revision_id,
            }
            return self._commit_operation(
                op,
                result,
                action="domain.publish",
                resource=domain_id,
                actor=actor,
                detail={"config_rev": new_rev_num, "revision_id": revision.revision_id},
            )
        except Exception:
            self.db.rollback()
            raise

    def rollback_domain(
        self,
        domain_id: str,
        actor: Actor | None,
        *,
        revision_id: str | None = None,
        expected_rev: int | None = None,
        idem_key: str = "",
        request_hash: str = "",
    ) -> dict[str, Any]:
        """回滚：复制指定历史 revision 为更大的新 revision，绝不倒退计数。"""
        digest = request_hash or content_hash({"op": "rollback", "revision_id": revision_id or ""})
        op, replay = self._start_operation(actor, "rollback", domain_id, idem_key, digest)
        if replay is not None:
            return replay
        try:
            row = self._require_instance(domain_id)
            expected = row.config_rev if expected_rev is None else int(expected_rev)
            if row.config_rev != expected:
                raise PreconditionFailed("配置已被其他操作更新，请刷新后重试")
            target_id = revision_id or self._latest_previous_revision(row)
            if not target_id:
                raise ValueError("没有可回滚的历史版本")
            target = self._revision_of(domain_id, target_id)
            current_code = self._current_code_version(domain_id)
            if target.code_version and current_code and target.code_version != current_code:
                raise ValueError("本机已安装代码版本与目标修订不一致，禁止回滚")
            new_rev_num = expected + 1
            revision = self._create_revision(
                domain_id,
                new_rev_num,
                dict(target.config),
                kind="rollback",
                actor=actor,
                restored_from=target.revision_id,
            )
            self._cas_update(
                domain_id,
                expected,
                status="online",
                active_revision_id=revision.revision_id,
                config=dict(target.config),
                version=str(target.config.get("version", row.version)),
                name=str(target.config.get("name", row.name)),
            )
            self.db.expire(row)
            self.db.refresh(row)
            self._sync_plugin_registry(row, publish=True)
            result = {
                "domain_id": domain_id,
                "status": "online",
                "version": row.version,
                "config_rev": new_rev_num,
                "active_revision_id": revision.revision_id,
                "restored_from": target.revision_id,
            }
            return self._commit_operation(
                op,
                result,
                action="domain.rollback",
                resource=domain_id,
                actor=actor,
                detail={"config_rev": new_rev_num, "restored_from": target.revision_id},
            )
        except Exception:
            self.db.rollback()
            raise

    def offline_domain(
        self,
        domain_id: str,
        actor: Actor | None,
        *,
        expected_rev: int | None = None,
        idem_key: str = "",
        request_hash: str = "",
    ) -> dict[str, Any]:
        digest = request_hash or content_hash({"op": "offline"})
        op, replay = self._start_operation(actor, "offline", domain_id, idem_key, digest)
        if replay is not None:
            return replay
        try:
            row = self._require_instance(domain_id)
            if row.status == "offline":
                raise ValueError("领域已下线")
            expected = row.config_rev if expected_rev is None else int(expected_rev)
            if row.config_rev != expected:
                raise PreconditionFailed("配置已被其他操作更新，请刷新后重试")
            active_config = self._active_config(row)
            new_rev_num = expected + 1
            revision = self._create_revision(
                domain_id, new_rev_num, active_config, kind="offline", actor=actor
            )
            self._cas_update(domain_id, expected, status="offline")
            self.db.expire(row)
            self.db.refresh(row)
            self._sync_plugin_registry(row, publish=False)
            result = {
                "domain_id": domain_id,
                "status": "offline",
                "version": row.version,
                "config_rev": new_rev_num,
                "revision_id": revision.revision_id,
            }
            return self._commit_operation(
                op,
                result,
                action="domain.offline",
                resource=domain_id,
                actor=actor,
                detail={"config_rev": new_rev_num},
            )
        except Exception:
            self.db.rollback()
            raise

    def _require_instance(self, domain_id: str) -> DomInstance:
        row = self.db.get(DomInstance, domain_id)
        if row is None:
            raise KeyError(f"领域实例不存在: {domain_id}")
        return row

    def _latest_previous_revision(self, row: DomInstance) -> str | None:
        rows = (
            self.db.execute(
                select(DomRevision)
                .where(DomRevision.domain_id == row.domain_id)
                .order_by(DomRevision.config_rev.desc())
            )
            .scalars()
            .all()
        )
        for rev in rows:
            if rev.revision_id != row.active_revision_id:
                return rev.revision_id
        return None

    def list_revisions(self, domain_id: str) -> list[dict[str, Any]]:
        self._require_instance(domain_id)
        rows = (
            self.db.execute(
                select(DomRevision)
                .where(DomRevision.domain_id == domain_id)
                .order_by(DomRevision.config_rev.desc())
            )
            .scalars()
            .all()
        )
        return [
            {
                "revision_id": r.revision_id,
                "config_rev": r.config_rev,
                "change_kind": r.change_kind,
                "content_hash": r.content_hash,
                "code_version": r.code_version,
                "restored_from": r.restored_from,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    def _sync_plugin_registry(self, row: DomInstance, *, publish: bool) -> None:
        """装配生效：把生效 revision 配置注入进程内注册表（完整刷新，不只改状态）。"""
        from pathlib import Path

        from core.domains import DomainPlugin
        from core.runtime import get_domain_registry

        config = self._active_config(row)
        registry = get_domain_registry()
        plugin = registry.get(row.domain_id)
        if plugin is None:
            plugin = DomainPlugin(
                domain_id=row.domain_id,
                name=row.name,
                version=row.version,
                status=row.status if publish else "offline",
                base_dir=Path("domains") / row.domain_id,
            )
            registry.register(plugin)
        plugin.status = row.status if publish else "offline"
        plugin.version = row.version
        plugin.name = row.name
        plugin.knowledge_binding = config.get("knowledge_binding", {})
        plugin.eval_config = config.get("eval_config", {})
        plugin.agent_config = config.get("agent_config", {})
        plugin.toolset_ref = config.get("toolset_ref", "")
        plugin.memory_policy = config.get("memory_policy", {})

    def list_domains(self) -> list[dict[str, Any]]:
        rows = self.db.execute(select(DomInstance)).scalars().all()
        return [
            {
                "domain_id": r.domain_id,
                "name": r.name,
                "status": r.status,
                "version": r.version,
                "config_rev": r.config_rev,
                "active_revision_id": r.active_revision_id,
                "draft_revision_id": r.draft_revision_id,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]

    def get_domain(self, domain_id: str) -> dict[str, Any]:
        row = self._require_instance(domain_id)
        config = self._active_config(row)
        revision_count = self.db.execute(
            select(DomRevision).where(DomRevision.domain_id == domain_id)
        ).scalars().all()
        return {
            "domain_id": row.domain_id,
            "name": row.name,
            "status": row.status,
            "version": row.version,
            "config": config,
            "config_rev": row.config_rev,
            "active_revision_id": row.active_revision_id,
            "draft_revision_id": row.draft_revision_id,
            "config_history_count": len(row.config_history or []),
            "revision_count": len(revision_count),
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
