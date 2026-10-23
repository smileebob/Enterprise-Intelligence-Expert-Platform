"""权限点定义与角色授权矩阵（FR-9-04 / 03 §3.5 RBAC）。

`角色 × 领域 × 工具` 授权矩阵：gov_role_permission ↔ 权限点；
智能体服务账号按领域最小授权。
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from core.abstraction.enums import Roles
from core.admin.models import GovPermission, GovRolePermission

# 权限点清单（resource.action，集中维护）
PERMISSIONS: list[dict[str, str]] = [
    {"perm_id": "chat.use", "resource": "chat", "action": "use", "scope": "domain", "description": "使用领域智能体对话"},
    {"perm_id": "retrieval.search", "resource": "retrieval", "action": "search", "scope": "domain", "description": "知识检索"},
    {"perm_id": "knowledge.read", "resource": "knowledge", "action": "read", "scope": "domain", "description": "知识查看（授权范围）"},
    {"perm_id": "knowledge.write", "resource": "knowledge", "action": "write", "scope": "domain", "description": "知识入库/审核"},
    {"perm_id": "knowledge.audit", "resource": "knowledge", "action": "audit", "scope": "all", "description": "知识审计"},
    {"perm_id": "conflict.resolve", "resource": "conflict", "action": "resolve", "scope": "domain", "description": "冲突裁决"},
    {"perm_id": "eval.run", "resource": "evaluation", "action": "run", "scope": "domain", "description": "执行六维评估"},
    {"perm_id": "eval.confirm", "resource": "evaluation", "action": "confirm", "scope": "owner", "description": "评估人工确认/修正"},
    {"perm_id": "eval.standard.manage", "resource": "eval_standard", "action": "manage", "scope": "domain", "description": "领域标准维护"},
    {"perm_id": "memory.mine.read", "resource": "memory_mine", "action": "read", "scope": "owner", "description": "本人记忆查阅"},
    {"perm_id": "memory.mine.write", "resource": "memory_mine", "action": "write", "scope": "owner", "description": "本人记忆更正/删除"},
    {"perm_id": "memory.read", "resource": "memory", "action": "read", "scope": "domain", "description": "企业级记忆查看"},
    {"perm_id": "memory.enterprise.manage", "resource": "memory", "action": "manage", "scope": "domain", "description": "企业级记忆管理/晋升审核"},
    {"perm_id": "tool.chemical.search", "resource": "tool", "action": "call", "scope": "domain", "description": "化学检索工具"},
    {"perm_id": "tool.chemical.evaluate", "resource": "tool", "action": "call", "scope": "domain", "description": "化学评估/合规工具"},
    {"perm_id": "tool.investment.search", "resource": "tool", "action": "call", "scope": "domain", "description": "招商检索工具"},
    {"perm_id": "domain.view", "resource": "domain_instance", "action": "read", "scope": "all", "description": "领域装配查看"},
    {"perm_id": "domain.assemble", "resource": "domain_instance", "action": "write", "scope": "all", "description": "领域装配（草稿）"},
    {"perm_id": "domain.publish", "resource": "domain_instance", "action": "publish", "scope": "all", "description": "领域上线/回滚"},
    {"perm_id": "role.manage", "resource": "role", "action": "manage", "scope": "all", "description": "角色权限管理"},
    {"perm_id": "audit.read", "resource": "audit", "action": "read", "scope": "all", "description": "审计查询"},
    {"perm_id": "growth.feedback", "resource": "growth", "action": "feedback", "scope": "domain", "description": "提交反馈"},
    {"perm_id": "growth.export", "resource": "growth", "action": "export", "scope": "domain", "description": "标注数据导出"},
    {"perm_id": "run.view_own", "resource": "run", "action": "view_own", "scope": "owner", "description": "查看本人运行"},
    {"perm_id": "run.inspect", "resource": "run", "action": "inspect", "scope": "domain", "description": "查看领域内运行时间线"},
    {"perm_id": "run.replay", "resource": "run", "action": "replay", "scope": "domain", "description": "冻结回放（05-B）"},
    {"perm_id": "run.fork", "resource": "run", "action": "fork", "scope": "domain", "description": "运行分叉（05-C）"},
    {"perm_id": "run.cancel", "resource": "run", "action": "cancel", "scope": "owner", "description": "取消运行"},
    {"perm_id": "run.payload.read", "resource": "run_payload", "action": "read", "scope": "domain", "description": "读取受控载荷"},
    {"perm_id": "tool.approve", "resource": "tool", "action": "approve", "scope": "domain", "description": "危险工具一次性审批"},
    {"perm_id": "output.review", "resource": "output", "action": "review", "scope": "domain", "description": "输出人工审核"},
]

ROLES: list[dict[str, str]] = [
    {"role_id": Roles.EXPERT, "name": "领域专家用户", "description": "一线业务人员（化学：技术工程师；生物：招商经理）"},
    {"role_id": Roles.KB_ADMIN, "name": "知识管理员", "description": "维护知识库与领域标准"},
    {"role_id": Roles.PLATFORM_ADMIN, "name": "平台管理员", "description": "装配领域实例、管理用户与权限"},
    {"role_id": Roles.SEC_AUDITOR, "name": "安全审计员", "description": "审查拦截记录、操作审计、脱敏合规"},
    {"role_id": Roles.AGENT, "name": "智能体服务账号", "description": "按领域最小授权的服务账号"},
]

# 角色 → 权限点（产品 02 §2.2 权限矩阵的代码化）
ROLE_GRANTS: dict[str, list[str]] = {
    Roles.EXPERT: [
        "chat.use",
        "retrieval.search",
        "knowledge.read",
        "eval.run",
        "eval.confirm",
        "memory.mine.read",
        "memory.mine.write",
        "memory.read",
        "tool.chemical.search",
        "tool.chemical.evaluate",
        "tool.investment.search",
        "domain.view",
        "growth.feedback",
        "run.view_own",
        "run.cancel",
    ],
    Roles.KB_ADMIN: [
        "chat.use",
        "retrieval.search",
        "knowledge.read",
        "knowledge.write",
        "knowledge.audit",
        "conflict.resolve",
        "eval.run",
        "eval.standard.manage",
        "memory.read",
        "memory.enterprise.manage",
        "domain.view",
        "growth.export",
        "growth.feedback",
        "run.view_own",
        "run.inspect",
        "run.replay",
        "tool.approve",
        "output.review",
    ],
    Roles.PLATFORM_ADMIN: [
        "chat.use",
        "retrieval.search",
        "knowledge.read",
        "eval.run",
        "eval.standard.manage",
        "memory.read",
        "memory.enterprise.manage",
        "domain.view",
        "domain.assemble",
        "domain.publish",
        "role.manage",
        "run.view_own",
        "run.inspect",
        "run.replay",
        "run.cancel",
        "tool.approve",
        "output.review",
    ],
    Roles.SEC_AUDITOR: [
        "knowledge.read",
        "knowledge.audit",
        "audit.read",
        "domain.view",
        "run.inspect",
    ],
    Roles.AGENT: [
        "chat.use",
        "retrieval.search",
        "knowledge.read",
        "eval.run",
        "memory.read",
        "memory.mine.write",
        "tool.chemical.search",
        "tool.chemical.evaluate",
        "tool.investment.search",
        "run.view_own",
    ],
}

# 角色 → 数据权限上限（1=外部可见, 2=内部可见）
ROLE_PERMISSION_MAX: dict[str, int] = {
    Roles.EXPERT: 2,
    Roles.KB_ADMIN: 2,
    Roles.PLATFORM_ADMIN: 2,
    Roles.SEC_AUDITOR: 1,  # 审计员不默认接触内部配比数据（最小权限）
    Roles.AGENT: 2,
}

# role_id -> (monotonic 写入时刻, 权限快照)；TTL 到期后回源 DB
_cache: dict[str, tuple[float, frozenset[str]]] = {}


def role_permissions(db: Session, role_id: str, *, fresh: bool = False) -> set[str]:
    """角色权限点集合（DB 为唯一权威；空集合代表真正无权限，不回退默认授权）。

    fresh=True 用于安全边界（准入/工具执行）：绕过进程缓存直接读 DB，保证跨进程撤权即时生效。
    """
    now = time.monotonic()
    cached = None if fresh else _cache.get(role_id)
    if cached is not None and now - cached[0] < get_settings().PERMISSION_CACHE_TTL_SECONDS:
        return set(cached[1])
    rows = db.execute(
        select(GovRolePermission.perm_id).where(GovRolePermission.role_id == role_id)
    ).scalars().all()
    perms = frozenset(rows)
    _cache[role_id] = (now, perms)
    return set(perms)


def invalidate_permission_cache() -> None:
    _cache.clear()


def permission_max_for_role(role_id: str) -> int:
    return ROLE_PERMISSION_MAX.get(role_id, 1)


def ensure_permission_tables(db: Session) -> None:
    """幂等写入权限点与角色授权（种子/启动校验用）。"""
    for perm in PERMISSIONS:
        if db.get(GovPermission, perm["perm_id"]) is None:
            db.add(GovPermission(**perm))
    db.flush()

    from core.admin.models import GovRole

    created_roles: list[str] = []
    for role in ROLES:
        if db.get(GovRole, role["role_id"]) is None:
            db.add(GovRole(role_id=role["role_id"], name=role["name"], description=role["description"]))
            created_roles.append(role["role_id"])
    db.flush()
    for role_id in created_roles:
        # 仅对本次新建角色做引导授权；已有角色即使授权为空也不回填（空集=真正无权限）
        for perm_id in ROLE_GRANTS.get(role_id, []):
            db.add(GovRolePermission(role_id=role_id, perm_id=perm_id))
    db.commit()
    invalidate_permission_cache()
