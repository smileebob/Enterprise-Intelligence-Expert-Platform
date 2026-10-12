"""融合与冲突消解（技术方案 03 §3.1 实现要点 3–6；BR-01/03/04 + CS-05 硬规则）。

- 融合优先级全序（确定性代码）：confirmed ≥ internal > stated > external > ai_inferred；raw 仅追溯；
- 权重融合 final_conf = base_priority × confidence × freshness_decay（freshest 用检索分近似）；
- 冲突检测：证据携带 conflict_ref 或与已登记冲突的 refs 对齐 → 挂载 Conflict 对象，双方保留（BR-04）；
- 合规例外：命中受限市场 + 受限物质 → 优先级反转 + 强制提示 + 无氟替代推荐（确定性规则，禁止 LLM 裁决）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.abstraction.dtos import (
    ConflictRecord,
    Evidence,
    FusedEvidence,
    ScoredChunk,
)
from core.abstraction.enums import SourceType
from core.domains import DomainPlugin
from core.knowledge.compliance import ComplianceOverride, evaluate_compliance_override

logger = logging.getLogger(__name__)

# 优先级基准分（融合权重融合用）
PRIORITY_BASE = {
    SourceType.CONFIRMED: 1.0,
    SourceType.INTERNAL: 0.9,
    SourceType.STATED: 0.8,
    SourceType.EXTERNAL: 0.6,
    SourceType.AI_INFERRED: 0.4,
    SourceType.DEPRECATED: 0.1,
    SourceType.RAW: 0.2,  # 仅作追溯依据，不直接参与融合
}


def priority_rank(plugin: DomainPlugin, source_type: str) -> int:
    order = plugin.fusion_priority
    try:
        return order.index(source_type)
    except ValueError:
        return len(order)  # 未知/raw/deprecated 排在最后


@dataclass
class FusionContext:
    plugin: DomainPlugin
    market: str = "unknown"
    need: dict[str, Any] | None = None
    query: str = ""
    fetch_missing: Callable[[list[str]], list[ScoredChunk]] | None = None
    products: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FusionResult(FusedEvidence):
    override: ComplianceOverride | None = None


def _record_id(chunk: ScoredChunk) -> str:
    return str((chunk.chunk.meta or {}).get("record_id", ""))


def _conflict_ref(chunk: ScoredChunk) -> str | None:
    return (chunk.chunk.meta or {}).get("conflict_ref")


def _weighted_confidence(chunk: ScoredChunk) -> float:
    conf = chunk.confidence if chunk.confidence is not None else 0.7
    return round(PRIORITY_BASE.get(chunk.source_type, 0.5) * conf * (0.4 + 0.6 * min(chunk.score, 1.0)), 6)


def order_evidence(plugin: DomainPlugin, scored: list[ScoredChunk]) -> list[ScoredChunk]:
    """确定性排序：优先级全序为主键，加权置信度为次键。raw/deprecated 沉底。"""
    def sort_key(chunk: ScoredChunk):
        rank = priority_rank(plugin, chunk.source_type)
        if chunk.source_type in (SourceType.RAW, SourceType.DEPRECATED):
            rank += 10
        return (rank, -_weighted_confidence(chunk))

    dedup: dict[str, ScoredChunk] = {}
    for chunk in scored:
        existing = dedup.get(chunk.chunk_id)
        if existing is None or chunk.score > existing.score:
            dedup[chunk.chunk_id] = chunk
    return sorted(dedup.values(), key=sort_key)


def match_conflicts(
    plugin: DomainPlugin,
    evidence: list[ScoredChunk],
    all_conflicts: list[ConflictRecord],
    query: str = "",
) -> list[ConflictRecord]:
    """冲突检测（两级判定，避免宽召回把全部外部标注冲突误挂）：

    1. 显式标注（证据 meta.conflict_ref）且**冲突的内部侧在场** → 命中；
    2. 登记冲突：需**双方证据均在本次证据集**，且冲突主题词与查询词元有交集。
    """
    from adapters.tokenize import tokenize

    record_ids = {_record_id(c) for c in evidence if _record_id(c)}
    explicit_refs = {_conflict_ref(c) for c in evidence if _conflict_ref(c)}
    query_tokens = set(tokenize(query)) if query else set()
    matched: list[ConflictRecord] = []
    for conflict in all_conflicts:
        internal_present = bool(set(conflict.internal_claim.refs) & record_ids)
        if conflict.conflict_id in explicit_refs:
            if internal_present:  # 仅外部侧带标注不构成「该主题卷入本次问答」
                matched.append(conflict)
            continue
        external_present = bool(set(conflict.external_claim.refs) & record_ids)
        if not (internal_present and external_present):
            continue
        if query_tokens:
            topic_tokens = set(tokenize(f"{conflict.topic} {conflict.title}"))
            if not (topic_tokens & query_tokens):
                continue
        matched.append(conflict)
    return matched


def _ensure_both_sides(
    evidence_by_id: dict[str, ScoredChunk],
    conflicts: list[ConflictRecord],
    fetch_missing: Callable[[list[str]], list[ScoredChunk]] | None,
) -> list[ScoredChunk]:
    """确保冲突双方来源均可引用（BR：双方保留、均引用）。"""
    present_record_ids = {_record_id(c) for c in evidence_by_id.values()}
    missing_refs: list[str] = []
    for conflict in conflicts:
        for claim in (conflict.internal_claim, conflict.external_claim):
            for ref in claim.refs:
                if ref not in present_record_ids:
                    missing_refs.append(ref)
    if not missing_refs or fetch_missing is None:
        return []
    fetched = fetch_missing(sorted(set(missing_refs)))
    added: list[ScoredChunk] = []
    for chunk in fetched:
        if chunk.chunk_id not in evidence_by_id:
            evidence_by_id[chunk.chunk_id] = chunk
            added.append(chunk)
    return added


def fuse_evidence(
    scored: list[ScoredChunk],
    all_conflicts: list[ConflictRecord],
    ctx: FusionContext,
) -> FusionResult:
    plugin = ctx.plugin
    ordered = order_evidence(plugin, scored)
    evidence_by_id = {c.chunk_id: c for c in ordered}

    # 1) 冲突挂载 + 补齐双方来源
    conflicts = match_conflicts(plugin, ordered, all_conflicts, query=ctx.query)
    if conflicts:
        _ensure_both_sides(evidence_by_id, conflicts, ctx.fetch_missing)

    # 2) 合规例外（确定性规则；命中则反转优先级并强制提示）
    override = ComplianceOverride(market=ctx.market)
    if plugin.compliance_override_enabled:
        override = evaluate_compliance_override(ctx.market, ctx.products)

    final_order = order_evidence(plugin, list(evidence_by_id.values()))
    if override.triggered:
        # 反转：合规类外部标准证据前置（平台级不变量）
        compliance_chunks = [c for c in final_order if c.chunk.category == plugin.compliance_category_tag]
        others = [c for c in final_order if c.chunk.category != plugin.compliance_category_tag]
        final_order = compliance_chunks + others

    # 3) 引用装配：C1..Cn 按最终顺序
    evidence: list[Evidence] = []
    for idx, chunk in enumerate(final_order, start=1):
        evidence.append(
            Evidence(
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                source_type=chunk.source_type,
                confidence=chunk.confidence,
                citation_id=f"C{idx}",
                source_ref=chunk.source_ref,
                score=chunk.score,
                category=chunk.category,
                meta=chunk.chunk.meta or {},
            )
        )

    # 4) 分区统计与说明
    partition_stats: dict[str, int] = {}
    for chunk in final_order:
        partition_stats[chunk.source_type] = partition_stats.get(chunk.source_type, 0) + 1

    notes: list[str] = []
    if conflicts:
        notes.append(f"检出 {len(conflicts)} 处冲突（{'、'.join(c.conflict_id for c in conflicts)}），已按内部优先裁决并标注")
    if override.triggered:
        notes.append(f"命中合规例外（{override.rule_id}）：以外部法规为准并推荐无氟替代")

    override_flags: list[dict[str, Any]] = []
    if override.triggered:
        override_flags.append(
            {
                "rule_id": override.rule_id,
                "market": override.market,
                "restricted_products": override.restricted_products,
                "recommend_products": override.recommend_products,
                "notice": override.notice,
            }
        )

    return FusionResult(
        evidence=evidence,
        conflicts=conflicts,
        partition_stats=partition_stats,
        override_flags=override_flags,
        notes=notes,
        override=override,
    )
