"""化学领域入库适配器：结构化 JSON 直导 → 知识块 + chem_* 结构化表 + 冲突对象。

对应产品 07 数据集五类知识文件（products/formulas/processes/experiment_cases/external_knowledge）。
切分策略：一个产品/一条配方/一份实验 = 一个知识块（不跨实体），source_ref 作引用锚点。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.tokenize import token_text
from core.abstraction.dtos import (
    ConflictClaims,
    ConflictRecord,
    KbChunkRecord,
    KbDocumentRecord,
)
from core.knowledge.ingest import IngestPackage, scan_sensitive
from domains.chemical.models import (
    ChemCustomerNeed,
    ChemExperiment,
    ChemFormula,
    ChemProcess,
    ChemProduct,
)

DOMAIN = "chemical"

_KNOWN_FILES = {
    "products": "产品主数据",
    "formulas": "工作液配方",
    "processes": "后整理工艺",
    "experiment_cases": "历史实验/试样报告",
    "external_knowledge": "外部专利/文献/标准",
}

# 法规/标准类外部条目 → compliance 标签（CS-05 反转依据）
COMPLIANCE_EXT_IDS = {"EXT-STD-005", "EXT-STD-006"}


def derive_compliance_level(sub_type: str, compliance_text: str) -> str:
    """产品合规等级：clean（无氟/无醛且符合 OEKO+ZDHC）/ ok / restricted_substance（如 C6）。"""
    blob = f"{sub_type} {compliance_text}"
    if "C6" in blob or "有机氟" in blob:
        return "restricted_substance"
    if "ZDHC" in blob:
        return "clean"
    return "ok"


def _kv_text(mapping: dict[str, Any] | None) -> str:
    if not mapping:
        return ""
    return "；".join(f"{k}：{v}" for k, v in mapping.items())


def _lists_text(values: list[Any] | None) -> str:
    return "、".join(str(v) for v in values or [])


def _make_doc(record_id: str, file_key: str, title: str, source_type: str, permission_level: int, source_ref: str, meta: dict) -> KbDocumentRecord:
    return KbDocumentRecord(
        doc_id=f"doc:{file_key}:{record_id}",
        title=title,
        domain_id=DOMAIN,
        modality="structured_json",
        source_type=source_type,
        permission_level=permission_level,
        origin=f"07数据集/{file_key}.json",
        version="v1",
        meta=meta,
    )


def _make_chunk(
    record_id: str,
    file_key: str,
    content: str,
    source_type: str,
    permission_level: int,
    source_ref: str,
    confidence: float | None,
    *,
    category: str | None = None,
    conflict_ref: str | None = None,
    sensitive_fields: list[str] | None = None,
    fields: dict[str, Any] | None = None,
) -> KbChunkRecord:
    meta: dict[str, Any] = {
        "record_id": record_id,
        "record_type": file_key,
        "fields": fields or {},
    }
    if conflict_ref:
        meta["conflict_ref"] = conflict_ref
    sensitive = sensitive_fields or scan_sensitive(content, fields)
    if sensitive:
        meta["sensitive_fields"] = sensitive
    return KbChunkRecord(
        chunk_id=f"chk:{file_key}:{record_id}",
        doc_id=f"doc:{file_key}:{record_id}",
        seq=0,
        content=content,
        domain_id=DOMAIN,
        source_type=source_type,
        category=category,
        confidence=confidence,
        permission_level=permission_level,
        source_ref=source_ref,
        status="ready",
        tokens=token_text(content),
        meta=meta,
    )


def build_ingest_package(db: Session, payload: dict[str, Any], source_name: str) -> IngestPackage:
    """按文件类型分发解析（source_name 形如 products.json）。解析保持纯函数（结构化写入见 write_structured_rows）。"""
    file_key = source_name.replace(".json", "")
    builders = {
        "products": _build_products,
        "formulas": _build_formulas,
        "processes": _build_processes,
        "experiment_cases": _build_experiments,
        "external_knowledge": _build_external,
    }
    builder = builders.get(file_key)
    if builder is None:
        raise KeyError(f"化学领域不支持的知识文件: {source_name}")
    return builder(payload)


SUPPORTED_SOURCES: tuple[str, ...] = (
    "products.json",
    "formulas.json",
    "processes.json",
    "experiment_cases.json",
    "external_knowledge.json",
    "conflict_scenarios.json",
)


def supported_sources() -> tuple[str, ...]:
    """本领域入库 API 支持的文件名（conflict_scenarios.json 走冲突基准装载路径）。"""
    return SUPPORTED_SOURCES


# ---------------- 五个文件各自的解析 ----------------
def _build_products(payload: dict[str, Any]) -> IngestPackage:
    package = IngestPackage()
    for p in payload.get("products", []):
        pid = p["product_id"]
        compliance_level = derive_compliance_level(p.get("sub_type", ""), p.get("compliance", ""))
        content = (
            f"产品[{pid}] {p.get('name', '')}：类别 {p.get('category', '')}（{p.get('sub_type', '')}）。"
            f"技术参数：{_kv_text(p.get('tech_params'))}。"
            f"关键性能：{_kv_text(p.get('performance'))}。"
            f"适用面料：{_lists_text(p.get('applicable_fabric'))}。"
            f"适用工艺：{p.get('process_ref', '')}。"
            f"合规声明：{p.get('compliance', '')}。"
            f"备注：{p.get('notes', '')}。数据来源：{p.get('data_source', '')}。"
        )
        package.documents.append(
            _make_doc(pid, "products", p.get("name", pid), "internal", 2, p.get("source_ref", ""), {"product_id": pid})
        )
        package.chunks.append(
            _make_chunk(
                pid,
                "products",
                content,
                "internal",
                2,
                p.get("source_ref", ""),
                0.9,
                fields={
                    "name": p.get("name", ""),
                    "category": p.get("category", ""),
                    "sub_type": p.get("sub_type", ""),
                    "process_ref": p.get("process_ref", ""),
                    "tech_params": p.get("tech_params", {}),
                    "performance": p.get("performance", {}),
                    "compliance_level": compliance_level,
                },
            )
        )
        if p.get("process_ref"):
            package.relations.append(
                {"from_entity": pid, "to_entity": p["process_ref"], "rel_type": "适用于", "source_type": "internal"}
            )
    return package


def _build_formulas(payload: dict[str, Any]) -> IngestPackage:
    package = IngestPackage()
    for f in payload.get("formulas", []):
        fid = f["formula_id"]
        comp = "；".join(
            f"{c.get('material')} {c.get('amount_g_per_L')}（{c.get('role')}）" for c in f.get("composition", [])
        )
        content = (
            f"工作液配方[{fid}] {f.get('name', '')}（对应产品 {f.get('product_id', '')}）："
            f"组成：{comp}。轧余率：{f.get('pickup', '')}。固化/焙烘：{f.get('cure', '')}。"
            f"备注：{f.get('notes', '')}。[内部专有配方]"
        )
        package.documents.append(
            _make_doc(fid, "formulas", f.get("name", fid), "internal", 2, f.get("source_ref", ""), {"formula_id": fid})
        )
        package.chunks.append(
            _make_chunk(
                fid,
                "formulas",
                content,
                "internal",
                2,
                f.get("source_ref", ""),
                float(f.get("confidence", 0.9)),
                sensitive_fields=["配方配比"],
                fields={"composition": f.get("composition", []), "cure": f.get("cure", ""), "product_id": f.get("product_id", "")},
            )
        )
        package.relations.append(
            {"from_entity": fid, "to_entity": f.get("product_id", ""), "rel_type": "对应", "source_type": "internal"}
        )
        # 配方组成中引用的其他产品（复配关系，支撑关联遍历）
        import re as _re

        for comp in f.get("composition", []):
            for ref in _re.findall(r"\b(TF-[A-Z]{1,4}-\d{3,4})\b", str(comp.get("material", ""))):
                if ref != f.get("product_id"):
                    package.relations.append(
                        {"from_entity": fid, "to_entity": ref, "rel_type": "复配", "source_type": "internal"}
                    )
    return package


def _build_processes(payload: dict[str, Any]) -> IngestPackage:
    package = IngestPackage()
    for proc in payload.get("processes", []):
        pid = proc["process_id"]
        content = (
            f"工艺[{pid}] {proc.get('name', '')}：方式 {proc.get('method', '')}。"
            f"条件：{_kv_text(proc.get('conditions'))}。"
            f"适用面料：{_lists_text(proc.get('applicable_fabric'))}。设备：{proc.get('equipment', '')}。"
            f"关键控制：{_lists_text(proc.get('key_controls'))}。"
            f"常见问题：{_lists_text(proc.get('common_issues'))}。备注：{proc.get('notes', '')}。"
        )
        package.documents.append(
            _make_doc(pid, "processes", proc.get("name", pid), "internal", 2, proc.get("source_ref", ""), {"process_id": pid})
        )
        package.chunks.append(
            _make_chunk(
                pid,
                "processes",
                content,
                "internal",
                2,
                proc.get("source_ref", ""),
                0.9,
                fields={"conditions": proc.get("conditions", {}), "common_issues": proc.get("common_issues", [])},
            )
        )
    return package


def _build_experiments(payload: dict[str, Any]) -> IngestPackage:
    package = IngestPackage()
    for e in payload.get("experiment_cases", []):
        cid = e["case_id"]
        content = (
            f"实验报告[{cid}] {e.get('title', '')}：面料 {e.get('fabric', '')}；产品 {e.get('product_id', '')}；"
            f"配方 {e.get('formula_id', '')}；工艺 {e.get('process', '')}。"
            f"结果：{_kv_text(e.get('results'))}。结论：{e.get('conclusion', '')}。"
            f"测试时间：{e.get('tested_at', '')}。备注：{e.get('notes', '')}。"
        )
        package.documents.append(
            _make_doc(cid, "experiment_cases", e.get("title", cid), "internal", 2, e.get("source_ref", ""), {"case_id": cid})
        )
        package.chunks.append(
            _make_chunk(
                cid,
                "experiment_cases",
                content,
                "internal",
                2,
                e.get("source_ref", ""),
                float(e.get("confidence", 0.9)),
                conflict_ref=e.get("conflict_ref"),
                fields={
                    "results": e.get("results", {}),
                    "conclusion": e.get("conclusion", ""),
                    "product_id": e.get("product_id", ""),
                    "formula_id": e.get("formula_id", ""),
                },
            )
        )
        if e.get("product_id"):
            package.relations.append(
                {"from_entity": cid, "to_entity": e["product_id"], "rel_type": "验证", "source_type": "internal"}
            )
        if e.get("formula_id"):
            package.relations.append(
                {"from_entity": cid, "to_entity": e["formula_id"], "rel_type": "被测试", "source_type": "internal"}
            )
    return package


def _build_external(payload: dict[str, Any]) -> IngestPackage:
    package = IngestPackage()
    for x in payload.get("external_knowledge", []):
        xid = x["ext_id"]
        category = "compliance" if xid in COMPLIANCE_EXT_IDS else None
        claims = "；".join(f"- {c}" for c in x.get("key_claims", []))
        content = (
            f"外部知识[{xid}｜{x.get('type', '')}] {x.get('title', '')}"
            f"（来源：{x.get('source', '')}，发布：{x.get('published_at', '')}）：要点：{claims}。"
            f"相关性：{x.get('relevance', '')}。"
        )
        package.documents.append(
            _make_doc(xid, "external_knowledge", x.get("title", xid), "external", 1, x.get("source_ref", ""), {"ext_id": xid})
        )
        package.chunks.append(
            _make_chunk(
                xid,
                "external_knowledge",
                content,
                "external",
                1,
                x.get("source_ref", ""),
                float(x.get("confidence", 0.6)),
                category=category,
                conflict_ref=x.get("conflict_ref"),
                fields={"type": x.get("type", ""), "key_claims": x.get("key_claims", [])},
            )
        )
    return package


# ---------------- 结构化表写入（chem_*） ----------------
def write_structured_rows(db: Session, payload: dict[str, Any], source_name: str) -> int:
    """将记录写入 chem_* 结构化表（幂等 upsert），返回写入行数。"""
    file_key = source_name.replace(".json", "")
    count = 0
    if file_key == "products":
        for p in payload.get("products", []):
            row = db.get(ChemProduct, p["product_id"]) or ChemProduct(product_id=p["product_id"])
            db.add(row)
            row.name = p.get("name", "")
            row.category = p.get("category", "")
            row.sub_type = p.get("sub_type", "")
            row.tech_params = p.get("tech_params", {})
            row.performance = p.get("performance", {})
            row.applicable_fabric = p.get("applicable_fabric", [])
            row.process_ref = p.get("process_ref")
            row.compliance = p.get("compliance", "")
            row.compliance_level = derive_compliance_level(p.get("sub_type", ""), p.get("compliance", ""))
            row.notes = p.get("notes", "")
            row.data_source = p.get("data_source", "")
            row.source_type = "internal"
            row.source_ref = p.get("source_ref", "")
            count += 1
    elif file_key == "formulas":
        for f in payload.get("formulas", []):
            row = db.get(ChemFormula, f["formula_id"]) or ChemFormula(formula_id=f["formula_id"])
            db.add(row)
            row.product_id = f.get("product_id", "")
            row.name = f.get("name", "")
            row.composition = f.get("composition", [])
            row.pickup = f.get("pickup", "")
            row.cure = f.get("cure", "")
            row.notes = f.get("notes", "")
            row.confidential = bool(f.get("confidential", True))
            row.source_type = "internal"
            row.source_ref = f.get("source_ref", "")
            row.confidence = f.get("confidence")
            count += 1
    elif file_key == "processes":
        for proc in payload.get("processes", []):
            row = db.get(ChemProcess, proc["process_id"]) or ChemProcess(process_id=proc["process_id"])
            db.add(row)
            row.name = proc.get("name", "")
            row.method = proc.get("method", "")
            row.conditions = proc.get("conditions", {})
            row.applicable_fabric = proc.get("applicable_fabric", [])
            row.equipment = proc.get("equipment", "")
            row.key_controls = proc.get("key_controls", [])
            row.common_issues = proc.get("common_issues", [])
            row.notes = proc.get("notes", "")
            row.source_type = "internal"
            row.source_ref = proc.get("source_ref", "")
            count += 1
    elif file_key == "experiment_cases":
        for e in payload.get("experiment_cases", []):
            _write_experiment(db, e)
            count += 1
    db.commit()
    return count


def _write_experiment(db: Session, e: dict[str, Any]) -> None:
    row = db.get(ChemExperiment, e["case_id"]) or ChemExperiment(case_id=e["case_id"])
    db.add(row)
    row.title = e.get("title", "")
    row.fabric = e.get("fabric", "")
    row.product_id = e.get("product_id")
    row.formula_id = e.get("formula_id")
    row.process = e.get("process", "")
    row.results = e.get("results", {})
    row.conclusion = e.get("conclusion", "")
    row.customer = e.get("customer", "")
    row.tested_at = e.get("tested_at", "")
    row.source_type = "internal"
    row.source_ref = e.get("source_ref", "")
    row.confidence = e.get("confidence")
    row.conflict_ref = e.get("conflict_ref")
    row.notes = e.get("notes", "")


# ---------------- 测试基准资产（不入知识库） ----------------
def seed_customer_needs(db: Session, payload: dict[str, Any]) -> int:
    """customer_queries.json → chem_customer_need（回归基准，不进入知识检索）。"""
    count = 0
    for q in payload.get("queries", []):
        need_id = f"need-{q['query_id'].lower()}"
        row = db.get(ChemCustomerNeed, need_id) or ChemCustomerNeed(need_id=need_id)
        db.add(row)
        row.query_id = q["query_id"]
        row.category = q.get("category", "")
        row.customer_need = q.get("customer_need", "")
        row.clarify_items = q.get("clarify_items", [])
        row.expected_answer_points = q.get("expected_answer_points", [])
        row.should_reference = q.get("should_reference", [])
        row.conflict_ref = q.get("conflict_ref")
        row.source_type = "stated"
        row.source_ref = f"客户咨询库/{q['query_id']}"
        count += 1
    db.commit()
    return count


def build_conflicts_from_scenarios(payload: dict[str, Any]) -> list[ConflictRecord]:
    """conflict_scenarios.json → ConflictRecord（入库为知识层冲突资产）。"""
    conflicts: list[ConflictRecord] = []
    for c in payload.get("conflict_scenarios", []):
        internal = c.get("internal_claim", {})
        external = c.get("external_claim", {})
        conflicts.append(
            ConflictRecord(
                conflict_id=c["conflict_id"],
                domain_id=DOMAIN,
                title=c.get("title", ""),
                topic=c.get("topic", ""),
                internal_claim=ConflictClaims(
                    source_type=internal.get("source_type", "internal"),
                    refs=list(internal.get("refs", [])),
                    statement=internal.get("statement", ""),
                    confidence=internal.get("confidence"),
                ),
                external_claim=ConflictClaims(
                    source_type=external.get("source_type", "external"),
                    refs=list(external.get("refs", [])),
                    statement=external.get("statement", ""),
                    confidence=external.get("confidence"),
                ),
                conflict_type=c.get("conflict_type", ""),
                expected_resolution=c.get("expected_resolution", {}),
                demo_query=c.get("demo_query", ""),
                trap=c.get("trap", ""),
            )
        )
    return conflicts


def collect_compliance_context(db: Session, record_ids: list[str]) -> list[dict[str, Any]]:
    """合规规则上下文：按记录 ID 收集产品（合规等级/类别/子类型），供 CS-05 确定性评估。"""
    if not record_ids:
        return []
    rows = db.execute(select(ChemProduct).where(ChemProduct.product_id.in_(record_ids))).scalars().all()
    return [
        {
            "product_id": row.product_id,
            "name": row.name,
            "category": row.category,
            "sub_type": row.sub_type,
            "compliance": row.compliance,
            "compliance_level": row.compliance_level,
        }
        for row in rows
    ]
