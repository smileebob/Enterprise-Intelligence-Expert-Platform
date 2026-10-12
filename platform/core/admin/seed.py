"""种子数据：4 角色 + 演示用户 + 权限矩阵 + 领域实例（幂等）。"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from core.abstraction.enums import Roles
from core.admin.models import DomInstance, GovUser
from core.gateway.permissions import ensure_permission_tables

logger = logging.getLogger(__name__)

DEMO_USERS = [
    {"user_id": "u-expert", "username": "expert", "name": "化学工程师（演示）", "role_id": Roles.EXPERT, "org": "功能化学事业部", "domain_scope": ["chemical"]},
    {"user_id": "u-kbadmin", "username": "kbadmin", "name": "知识管理员（演示）", "role_id": Roles.KB_ADMIN, "org": "知识运营中心", "domain_scope": ["chemical", "investment"]},
    {"user_id": "u-admin", "username": "admin", "name": "平台管理员（演示）", "role_id": Roles.PLATFORM_ADMIN, "org": "数字化平台部", "domain_scope": ["chemical", "investment"]},
    {"user_id": "u-auditor", "username": "auditor", "name": "安全审计员（演示）", "role_id": Roles.SEC_AUDITOR, "org": "安全合规部", "domain_scope": ["chemical"]},
]


def _password_for(role_id: str) -> str:
    settings = get_settings()
    return {
        Roles.EXPERT: settings.SEED_EXPERT_PASSWORD,
        Roles.KB_ADMIN: settings.SEED_KB_PASSWORD,
        Roles.PLATFORM_ADMIN: settings.SEED_ADMIN_PASSWORD,
        Roles.SEC_AUDITOR: settings.SEED_AUDIT_PASSWORD,
    }.get(role_id, settings.SEED_EXPERT_PASSWORD)


def seed_users(db: Session) -> int:
    from core.admin.security import hash_password

    ensure_permission_tables(db)
    count = 0
    for spec in DEMO_USERS:
        user = db.get(GovUser, spec["user_id"])
        if user is None:
            user = GovUser(
                user_id=spec["user_id"],
                username=spec["username"],
                password_hash=hash_password(_password_for(spec["role_id"])),
                name=spec["name"],
                org=spec["org"],
                role_id=spec["role_id"],
                domain_scope=spec["domain_scope"],
            )
            db.add(user)
            count += 1
    db.commit()
    logger.info("seed users: +%d", count)
    return count


def seed_agents(db: Session) -> int:
    """智能体服务账号（按领域最小授权，03 §3.5）。"""
    from core.admin.security import hash_password

    if db.get(GovUser, "u-agent") is None:
        db.add(
            GovUser(
                user_id="u-agent",
                username="agent-service",
                password_hash=hash_password("Agent@disabled-local"),
                name="智能体服务账号",
                org="platform",
                role_id=Roles.AGENT,
                domain_scope=["chemical", "investment"],
            )
        )
        db.commit()
        return 1
    return 0


def seed_domains(db: Session) -> int:
    """写入两个领域实例装配（化学 online / 招商 draft→可一键 online）。"""
    from pathlib import Path

    import yaml

    count = 0
    for domain_id, status in (("chemical", "online"), ("investment", "draft")):
        yaml_path = Path("domains") / domain_id / "domain.yaml"
        if not yaml_path.exists():
            continue
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        row = db.get(DomInstance, domain_id)
        if row is None:
            row = DomInstance(domain_id=domain_id, name=config.get("name", domain_id))
            db.add(row)
            count += 1
        row.name = config.get("name", domain_id)
        row.status = status
        row.config = config
        row.version = str(config.get("version", "0.1.0"))
        row.created_by = "seed"
        row.config_history = row.config_history or []
    db.commit()
    logger.info("seed domains: +%d", count)
    return count


def seed_standards(db: Session) -> int:
    """为已装配领域播种评估标准初始 active 版本（幂等：已有该标准或已有 active 则跳过）。

    标准内容取自领域插件 eval_config（domain.yaml），与运行时权重来源一致，
    供 BR-09 版本回溯（draft→active→archived）与人工重算入口使用。
    """
    import re

    from core.abstraction.dtos import EvalStandardRecord
    from core.runtime import eval_repo, get_domain_registry

    repo = eval_repo(db)
    count = 0
    for plugin in get_domain_registry().all():
        object_type = str(plugin.eval_config.get("object_type", "solution"))
        standard_ref = plugin.standard_ref or f"std-{plugin.domain_id}-v1"
        if repo.get_standard(standard_ref) is not None:
            continue
        if repo.get_active_standard(plugin.domain_id, object_type) is not None:
            continue
        year_match = re.search(r"(\d{4})", standard_ref)
        repo.upsert_standard(
            EvalStandardRecord(
                standard_id=standard_ref,
                domain_id=plugin.domain_id,
                year=int(year_match.group(1)) if year_match else 2026,
                object_type=object_type,
                sub_domain=None,
                standard_desc=f"{plugin.name} 领域标准初始版本（装配自 domain.yaml eval_config）",
                dimension_weights={str(d.get("code", "")): d.get("default_weight", 0) for d in plugin.dimensions},
                scoring_rubric={},
                scenario_profiles=plugin.scenario_profiles,
                sample_size=0,
                version=standard_ref,
                status="active",
                reviewed_by="seed",
                meta={"seeded_from": "domain.yaml eval_config"},
            )
        )
        count += 1
    db.commit()
    logger.info("seed standards: +%d", count)
    return count


def seed_all(db: Session) -> dict[str, int]:
    stats = {
        "users": seed_users(db),
        "agents": seed_agents(db),
        "domains": seed_domains(db),
        "standards": seed_standards(db),
    }
    return stats
