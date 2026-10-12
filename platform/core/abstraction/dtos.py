"""M0 抽象层 DTO：跨层传递的数据对象（不绑定存储与模型 SDK）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------- 知识域 ----------------
@dataclass
class KbDocumentRecord:
    doc_id: str
    title: str
    domain_id: str
    modality: str = "structured_json"  # text/table/image/pdf/structured_json
    source_type: str = "internal"
    permission_level: int = 1
    origin: str = ""
    version: str = "v1"
    collected_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class KbChunkRecord:
    chunk_id: str
    doc_id: str
    seq: int
    content: str
    domain_id: str
    source_type: str = "internal"
    category: str | None = None
    confidence: float | None = None
    permission_level: int = 1
    source_ref: str = ""
    status: str = "pending"
    tokens: str = ""  # jieba 预分词结果（空格分隔）
    embedding: list[float] | None = None
    embedding_model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoredChunk:
    chunk: KbChunkRecord
    score: float = 0.0
    vector_score: float = 0.0
    lexical_score: float = 0.0

    # 透传常用字段，减少嵌套访问
    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id

    @property
    def content(self) -> str:
        return self.chunk.content

    @property
    def source_type(self) -> str:
        return self.chunk.source_type

    @property
    def source_ref(self) -> str:
        return self.chunk.source_ref

    @property
    def confidence(self) -> float | None:
        return self.chunk.confidence

    @property
    def category(self) -> str | None:
        return self.chunk.category


@dataclass
class RetrievalQuery:
    query: str
    domain_id: str
    query_vec: list[float] | None = None
    # 分区召回：按插件 default_partition_order 决定顺序；None=不过滤
    source_types: list[str] | None = None
    permission_max: int = 1
    k_internal: int = 6
    k_external: int = 6
    status: list[str] = field(default_factory=lambda: ["ready"])
    vector_weight: float = 0.6
    lexical_weight: float = 0.4
    extra_filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConflictClaims:
    source_type: str
    refs: list[str] = field(default_factory=list)
    statement: str = ""
    confidence: float | None = None


@dataclass
class ConflictRecord:
    conflict_id: str
    domain_id: str
    title: str
    topic: str
    internal_claim: ConflictClaims
    external_claim: ConflictClaims
    conflict_type: str = ""
    expected_resolution: dict[str, Any] = field(default_factory=dict)
    demo_query: str = ""
    trap: str = ""
    status: str = "open"
    resolution: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Evidence:
    """融合证据：每条带六级标签与可追溯引用（citation_id 为回合内序号）。"""

    chunk_id: str
    content: str
    source_type: str
    confidence: float | None
    citation_id: str = ""
    source_ref: str = ""
    score: float = 0.0
    category: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FusedEvidence:
    evidence: list[Evidence] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    partition_stats: dict[str, int] = field(default_factory=dict)
    override_flags: list[dict[str, Any]] = field(default_factory=list)  # 合规例外等确定性规则命中
    notes: list[str] = field(default_factory=list)


# ---------------- 记忆域 ----------------
@dataclass
class MemoryItemRecord:
    memory_id: str
    layer: str
    owner_id: str
    content: str
    domain_id: str | None = None
    permission_scope: str = "owner"
    source_type: str = "ai_inferred"
    confidence: float = 0.6
    reuse_count: int = 0
    last_used_at: str | None = None
    promoted_from: str | None = None
    promotion_basis: str | None = None
    derived_from: list[str] | None = None
    status: str = "active"
    embedding: list[float] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryCandidate:
    layer: str
    owner_id: str
    content: str
    domain_id: str | None = None
    permission_scope: str = "owner"
    source_type: str = "ai_inferred"
    confidence: float = 0.6
    origin: str = "agent"  # agent / feedback / manual
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------- 评估域 ----------------
@dataclass
class EvalStandardRecord:
    standard_id: str
    domain_id: str
    year: int
    object_type: str
    sub_domain: str | None
    standard_desc: str
    dimension_weights: dict[str, Any]
    scoring_rubric: dict[str, Any]
    scenario_profiles: dict[str, dict[str, int]] = field(default_factory=dict)
    sample_size: int = 0
    version: str = ""
    status: str = "active"
    reviewed_by: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------- 治理域 ----------------
@dataclass
class AuditEntry:
    trace_id: str
    action: str
    actor_id: str | None = None
    resource: str | None = None
    hook_type: str | None = None
    decision: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------- 工具契约（MCP 同构，§2.5） ----------------
@dataclass
class Actor:
    """调用主体（来自 JWT 认证；智能体服务账号亦以本结构表示）。"""

    user_id: str
    username: str = ""
    role_id: str = ""
    permission_max: int = 1  # 数据权限：1=外部可见, 2=内部可见
    domain_scope: list[str] = field(default_factory=lambda: ["chemical"])


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: str
    risk_level: str = "normal"
    domain: str = ""
    rate_limit: str | None = None  # 如 "10/minute"


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    message: str = ""
    meta: dict[str, Any] = field(default_factory=dict)  # 须含 source_type/source_ref/fetched_at


@dataclass
class ChatResult:
    content: str
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    degrade_level: str = "L0"
    raw: dict[str, Any] = field(default_factory=dict)
