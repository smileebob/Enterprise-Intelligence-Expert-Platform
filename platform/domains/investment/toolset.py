"""招商领域工具集（轻量）：企业检索、园区资源匹配与资质核实（外部系统预留插口）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from core.abstraction.dtos import ToolResult
from core.clock import iso, utc_now
from core.gateway.registry import tool
from core.gateway.service import ToolCtx
from domains.investment.models import InvEnterprise, InvParkResource, InvProfile

DOMAIN = "investment"

# 资质核实项与来源规则（模拟外部数据源；接真实系统时替换为连接器调用，接口契约不变）
QUALIFICATION_ITEMS = ("工商登记", "高新技术企业认证", "融资轮次一致性")


@tool(
    name="search_enterprises",
    description="按产业方向/阶段/区域检索企业库（含画像与数据充分度）",
    input_schema={
        "type": "object",
        "properties": {
            "track": {"type": "string"},
            "stage": {"type": "string"},
            "region": {"type": "string"},
            "query": {"type": "string"},
        },
        "required": [],
    },
    permission="tool.investment.search",
    domain=DOMAIN,
)
async def search_enterprises(ctx: ToolCtx, track: str = "", stage: str = "", region: str = "", query: str = "") -> ToolResult:
    rows = ctx.db.execute(select(InvEnterprise)).scalars().all()
    hits: list[dict[str, Any]] = []
    for row in rows:
        if track and track not in row.track:
            continue
        if stage and stage not in row.stage:
            continue
        if region and region not in row.region:
            continue
        profile = ctx.db.get(InvProfile, f"PROF-{row.ent_id}")
        blob = f"{row.name} {row.track} {' '.join(row.aliases or [])}"
        if query and not any(token in blob for token in query.split()):
            continue
        hits.append(
            {
                "ent_id": row.ent_id,
                "name": row.name,
                "track": row.track,
                "stage": row.stage,
                "region": row.region,
                "six_dim": profile.six_dim if profile else {},
                "data_completeness": float(profile.data_completeness) if profile and profile.data_completeness is not None else 0,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
            }
        )
    return ToolResult(ok=True, data=hits, message=f"命中 {len(hits)} 家企业", meta={"source_type": "external", "fetched_at": iso(utc_now())})


@tool(
    name="match_park_resources",
    description="按企业需求匹配园区空间/政策/产业资源",
    input_schema={
        "type": "object",
        "properties": {"ent_id": {"type": "string"}, "needs": {"type": "array", "items": {"type": "string"}}},
        "required": ["ent_id"],
    },
    permission="tool.investment.search",
    domain=DOMAIN,
)
async def match_park_resources(ctx: ToolCtx, ent_id: str, needs: list[str] | None = None) -> ToolResult:
    resources = ctx.db.execute(select(InvParkResource)).scalars().all()
    hits = [
        {
            "resource_id": row.resource_id,
            "type": row.type,
            "name": row.name,
            "attributes": row.attributes,
            "availability": row.availability,
            "source_type": "internal",
            "source_ref": f"园区资源库/{row.resource_id}",
        }
        for row in resources
    ]
    return ToolResult(
        ok=True,
        data={"ent_id": ent_id, "needs": needs or [], "resources": hits},
        message=f"匹配 {len(hits)} 项园区资源",
        meta={"source_type": "internal", "fetched_at": iso(utc_now())},
    )


@tool(
    name="verify_qualification",
    description="企业资质核实（外部核实系统预留插口；当前为模拟数据源，可整体替换为真实工商/认证系统连接器）",
    input_schema={
        "type": "object",
        "properties": {
            "ent_id": {"type": "string", "description": "企业 ID"},
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"核实项（默认全部：{'、'.join(QUALIFICATION_ITEMS)}）",
            },
        },
        "required": ["ent_id"],
    },
    permission="tool.investment.search",  # 只读核实类复用检索权限；如需独立授权点，见 tools/investment_tools.yaml 注
    domain=DOMAIN,
)
async def verify_qualification(ctx: ToolCtx, ent_id: str, items: list[str] | None = None) -> ToolResult:
    """核验企业资质（模拟外部数据源，确定性输出；真实接入时在 _verify_item 内替换为连接器调用）。

    每条结果附来源 + 获取时间 + 置信度（FR-5-04）；未覆盖的核实项显式返回 unsupported，
    不做臆测（与「无据不下结论」约束一致）。
    """
    checked_at = iso(utc_now())
    ent = ctx.db.get(InvEnterprise, ent_id)
    if ent is None:
        return ToolResult(
            ok=True,
            data={"ent_id": ent_id, "overall": "not_found", "items": []},
            message=f"未在企业主数据中找到主体 {ent_id}，建议人工核验",
            meta={"source_type": "external", "source_ref": "资质核实（模拟数据源）", "fetched_at": checked_at},
        )
    profile = ctx.db.get(InvProfile, f"PROF-{ent_id}")
    wanted = list(items) if items else list(QUALIFICATION_ITEMS)
    results = [_verify_item(name, ent, profile, checked_at) for name in wanted]
    verified = [r for r in results if r["status"] == "verified"]
    if results and len(verified) == len(results):
        overall = "verified"
    elif verified:
        overall = "partial"
    else:
        overall = "unverified"
    return ToolResult(
        ok=True,
        data={"ent_id": ent_id, "name": ent.name, "overall": overall, "checked_at": checked_at, "items": results},
        message=f"资质核实完成：{len(verified)}/{len(results)} 项通过（模拟数据源）",
        meta={
            "source_type": "external",
            "source_ref": f"资质核实（模拟数据源）/{ent_id}",
            "fetched_at": checked_at,
            "external_connector": "预留：接入工商/认证/投融资系统的连接器后替换 _verify_item 实现，接口契约不变",
        },
    )


def _verify_item(name: str, ent: InvEnterprise, profile: InvProfile | None, checked_at: str) -> dict[str, Any]:
    """单项核实（模拟规则，确定性可复现）。"""
    six_dim = (profile.six_dim if profile else None) or {}
    if name == "工商登记":
        return {
            "item": name,
            "status": "verified" if ent.unified_credit_code else "unavailable",
            "detail": (
                f"统一社会信用代码 {ent.unified_credit_code}（登记名称：{ent.name}）"
                if ent.unified_credit_code
                else "缺少统一社会信用代码，无法核验"
            ),
            "source": "企业信息公示系统（模拟）",
            "confidence": 0.9,
            "fetched_at": checked_at,
        }
    if name == "高新技术企业认证":
        tech = float(six_dim.get("E1", 0) or 0)
        return {
            "item": name,
            "status": "verified" if tech >= 80 else "unverified",
            "detail": f"技术先进性评分 {tech}（模拟阈值 ≥80）",
            "source": "高新技术企业认定库（模拟）",
            "confidence": 0.75,
            "fetched_at": checked_at,
        }
    if name == "融资轮次一致性":
        funding = (profile.funding_stage if profile else "") or ""
        consistent = bool(funding) and bool(ent.stage) and funding.startswith(ent.stage)
        return {
            "item": name,
            "status": "verified" if consistent else ("unavailable" if not funding else "unverified"),
            "detail": f"企业主数据阶段 {ent.stage or '未知'}；投融资记录 {funding or '缺失'}",
            "source": "投融资信息库（模拟）",
            "confidence": 0.7,
            "fetched_at": checked_at,
        }
    return {
        "item": name,
        "status": "unsupported",
        "detail": "外部核实系统暂未覆盖该核实项（预留扩展）",
        "source": "",
        "confidence": 0.0,
        "fetched_at": checked_at,
    }
