"""平台共享枚举（术语与产品 04/技术方案完全一致）。"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """六级信息分级（04-领域数据模型 §二）。"""

    RAW = "raw"
    INTERNAL = "internal"
    EXTERNAL = "external"
    STATED = "stated"
    AI_INFERRED = "ai_inferred"
    CONFIRMED = "confirmed"
    DEPRECATED = "deprecated"


# 融合优先级全序（confirmed ≥ internal > stated > external > ai_inferred；raw 不直接参与融合）
FUSION_PRIORITY: list[str] = [
    SourceType.CONFIRMED,
    SourceType.INTERNAL,
    SourceType.STATED,
    SourceType.EXTERNAL,
    SourceType.AI_INFERRED,
]


class ChunkStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class IngestStatus(StrEnum):
    PARSING = "parsing"
    PENDING_REVIEW = "pending_review"
    READY = "ready"
    FAILED = "failed"


class ConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    PENDING_EVIDENCE = "pending_evidence"


class MemoryLayer(StrEnum):
    SESSION = "session"
    USER = "user"
    ENTERPRISE = "enterprise"
    DOMAIN = "domain"


class PermissionScope(StrEnum):
    OWNER = "owner"
    TEAM = "team"
    DOMAIN = "domain"
    ALL = "all"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DomainStatus(StrEnum):
    DRAFT = "draft"
    ONLINE = "online"
    OFFLINE = "offline"


class StandardStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class RiskLevel(StrEnum):
    NORMAL = "normal"
    DANGEROUS = "dangerous"


class HookStage(StrEnum):
    INPUT = "input"
    RETRIEVAL = "retrieval"
    TOOL = "tool"
    OUTPUT = "output"
    MEMORY_WRITE = "memory_write"


class HookAction(StrEnum):
    PASS = "pass"  # noqa: S105
    MASK = "mask"
    BLOCK = "block"
    CONFIRM = "confirm"


class FeedbackRating(StrEnum):
    ADOPTED = "adopted"
    MODIFIED = "modified"
    REJECTED = "rejected"


class DegradeLevel(StrEnum):
    """可用性降级层级（技术方案 06 §6.5）。"""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    REPLAY = "replay"


class Roles(StrEnum):
    """平台四角色（PRD 2.1）+ 智能体服务账号。"""

    EXPERT = "domain_expert"  # 领域专家用户
    KB_ADMIN = "kb_admin"  # 知识管理员
    PLATFORM_ADMIN = "platform_admin"  # 平台管理员
    SEC_AUDITOR = "sec_auditor"  # 安全审计员
    AGENT = "agent_service"  # 智能体服务账号
