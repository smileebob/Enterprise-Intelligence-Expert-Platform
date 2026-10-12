"""化学领域工具集（④ 领域工具，MCP 同构契约，技术方案 02 §2.5）。

工具结果每条携带 source_type / source_ref / confidence（供融合与引用装配）；
外部数据工具强制标注来源与获取时间（FR-5-04）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from core.abstraction.dtos import ToolResult
from core.clock import iso, utc_now
from core.gateway.registry import tool
from core.gateway.service import ToolCtx
from core.knowledge.compliance import evaluate_compliance_override
from domains.chemical.models import ChemExperiment, ChemFormula, ChemProduct

DOMAIN = "chemical"


def _product_brief(row: ChemProduct) -> dict[str, Any]:
    return {
        "product_id": row.product_id,
        "name": row.name,
        "category": row.category,
        "sub_type": row.sub_type,
        "performance": row.performance,
        "applicable_fabric": row.applicable_fabric,
        "compliance": row.compliance,
        "compliance_level": row.compliance_level,
        "process_ref": row.process_ref,
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "confidence": float(row.confidence) if row.confidence is not None else 0.9,
    }


@tool(
    name="search_products",
    description="按面料/性能/类别关键词检索内部产品主数据",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "fabric": {"type": "string"},
            "category": {"type": "string"},
        },
        "required": ["query"],
    },
    permission="tool.chemical.search",
    domain=DOMAIN,
)
async def search_products(ctx: ToolCtx, query: str, fabric: str = "", category: str = "") -> ToolResult:
    rows = ctx.db.execute(select(ChemProduct)).scalars().all()
    hits = []
    for row in rows:
        blob = f"{row.product_id} {row.name} {row.category} {row.sub_type} {row.notes} {row.performance}"
        if category and category not in row.category:
            continue
        if fabric and row.applicable_fabric and fabric not in "、".join(row.applicable_fabric):
            continue
        if not query or any(token in blob for token in _tokens(query)):
            hits.append(_product_brief(row))
        if len(hits) >= 6:
            break
    return ToolResult(
        ok=True,
        data=hits,
        message=f"命中 {len(hits)} 个产品",
        meta={"source_type": "internal", "fetched_at": iso(utc_now())},
    )


@tool(
    name="search_formulas",
    description="按产品/用途检索内部工作液配方（配方配比为敏感字段，按角色脱敏）",
    input_schema={
        "type": "object",
        "properties": {"product_id": {"type": "string"}, "keyword": {"type": "string"}},
        "required": [],
    },
    permission="tool.chemical.search",
    domain=DOMAIN,
)
async def search_formulas(ctx: ToolCtx, product_id: str = "", keyword: str = "") -> ToolResult:
    rows = ctx.db.execute(select(ChemFormula)).scalars().all()
    hits = []
    for row in rows:
        if product_id and row.product_id != product_id:
            continue
        blob = f"{row.name} {row.notes} {row.composition}"
        if keyword and keyword not in blob:
            continue
        hits.append(
            {
                "formula_id": row.formula_id,
                "product_id": row.product_id,
                "name": row.name,
                "composition": row.composition,
                "cure": row.cure,
                "pickup": row.pickup,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "confidence": float(row.confidence) if row.confidence is not None else 0.9,
                "sensitive_fields": ["配方配比"] if row.confidential else [],
            }
        )
    return ToolResult(ok=True, data=hits, message=f"命中 {len(hits)} 条配方", meta={"source_type": "internal", "fetched_at": iso(utc_now())})


@tool(
    name="search_experiments",
    description="按产品/面料/性能关键词检索内部历史实验报告",
    input_schema={
        "type": "object",
        "properties": {"product_id": {"type": "string"}, "fabric": {"type": "string"}, "metric": {"type": "string"}},
        "required": [],
    },
    permission="tool.chemical.search",
    domain=DOMAIN,
)
async def search_experiments(ctx: ToolCtx, product_id: str = "", fabric: str = "", metric: str = "") -> ToolResult:
    rows = ctx.db.execute(select(ChemExperiment)).scalars().all()
    hits = []
    for row in rows:
        if product_id and row.product_id != product_id:
            continue
        if fabric and fabric not in row.fabric:
            continue
        blob = f"{row.title} {row.conclusion} {row.results}"
        if metric and metric not in blob:
            continue
        hits.append(
            {
                "case_id": row.case_id,
                "title": row.title,
                "fabric": row.fabric,
                "product_id": row.product_id,
                "formula_id": row.formula_id,
                "process": row.process,
                "results": row.results,
                "conclusion": row.conclusion,
                "conflict_ref": row.conflict_ref,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "confidence": float(row.confidence) if row.confidence is not None else 0.9,
            }
        )
    return ToolResult(ok=True, data=hits, message=f"命中 {len(hits)} 份实验报告", meta={"source_type": "internal", "fetched_at": iso(utc_now())})


@tool(
    name="search_external",
    description="检索外部专利/文献/标准（返回强制标注来源与获取时间）",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}, "type": {"type": "string"}},
        "required": ["query"],
    },
    permission="tool.chemical.search",
    domain=DOMAIN,
)
async def search_external(ctx: ToolCtx, query: str, type: str = "") -> ToolResult:
    from core.guardrails.retrieval_hook import derive_permission_max
    from core.runtime import knowledge_repo

    repo = knowledge_repo(ctx.db)
    from core.abstraction.dtos import RetrievalQuery

    scored = repo.search(
        RetrievalQuery(
            query=query,
            domain_id=DOMAIN,
            source_types=["external"],
            permission_max=derive_permission_max(ctx.actor),
            k_internal=5,
            k_external=5,
        )
    )
    hits = []
    for item in scored[:5]:
        meta = item.chunk.meta or {}
        if type and type not in str(meta.get("fields", {}).get("type", "")):
            continue
        hits.append(
            {
                "ext_id": meta.get("record_id", ""),
                "type": meta.get("fields", {}).get("type", ""),
                "content": item.content,
                "source_type": "external",
                "source_ref": item.source_ref,
                "confidence": item.confidence,
            }
        )
    return ToolResult(
        ok=True,
        data=hits,
        message=f"命中 {len(hits)} 条外部知识",
        meta={"source_type": "external", "fetched_at": iso(utc_now())},
    )


@tool(
    name="score_solution",
    description="对方案执行六维评估（调用评估引擎，含合规校验）",
    input_schema={
        "type": "object",
        "properties": {"object_ref": {"type": "object"}, "scenario": {"type": "string"}},
        "required": ["object_ref"],
    },
    permission="tool.chemical.evaluate",
    domain=DOMAIN,
)
async def score_solution(ctx: ToolCtx, object_ref: dict[str, Any], scenario: str = "默认") -> ToolResult:
    from core.evaluation.engine import EvaluationEngine

    engine = EvaluationEngine(ctx.db, gateway=ctx.gateway, registry=ctx.registry)
    result = await engine.evaluate(
        {
            "domain_id": DOMAIN,
            "object_type": "solution",
            "object_ref": object_ref,
            "scenario": scenario,
        },
        actor=ctx.actor,
        idempotency_key=None,
        persist=False,
    )
    return ToolResult(
        ok=True,
        data=result,
        message=f"六维总分 {result.get('total_score')}",
        meta={"source_type": "internal", "fetched_at": iso(utc_now())},
    )


@tool(
    name="check_compliance",
    description="合规校验（目标市场法规 vs 产品合规等级，CS-05 合规例外）",
    input_schema={
        "type": "object",
        "properties": {
            "product_ids": {"type": "array", "items": {"type": "string"}},
            "target_market": {"type": "string"},
        },
        "required": ["product_ids"],
    },
    permission="tool.chemical.evaluate",
    domain=DOMAIN,
)
async def check_compliance(ctx: ToolCtx, product_ids: list[str], target_market: str = "") -> ToolResult:
    from core.knowledge.compliance import detect_market
    from domains.chemical.ingest_adapter import collect_compliance_context

    products = collect_compliance_context(ctx.db, product_ids)
    market_text = target_market or str((ctx.extra or {}).get("market", ""))
    market = detect_market(market_text) if market_text else "unknown"
    override = evaluate_compliance_override(market, products)
    return ToolResult(
        ok=True,
        data={
            "market": market,
            "products": products,
            "compliance_triggered": override.triggered,
            "rule_id": override.rule_id,
            "notice": override.notice,
            "recommend_products": override.recommend_products,
        },
        message="合规校验完成",
        meta={"source_type": "external", "fetched_at": iso(utc_now())},
    )


def _tokens(text: str) -> list[str]:
    from adapters.tokenize import tokenize

    return tokenize(text)
