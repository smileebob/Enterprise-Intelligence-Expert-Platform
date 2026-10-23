"""招商领域入库适配器（轻量）：企业/画像/园区资源/政策 → 知识块 + inv_* 结构化表。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from adapters.tokenize import token_text
from core.abstraction.dtos import KbChunkRecord, KbDocumentRecord
from core.knowledge.ingest import IngestPackage
from domains.investment.models import InvEnterprise, InvParkResource, InvPolicy, InvProfile

DOMAIN = "investment"


def _doc(record_id: str, kind: str, title: str, source_ref: str) -> KbDocumentRecord:
    return KbDocumentRecord(
        doc_id=f"doc:{kind}:{record_id}",
        title=title,
        domain_id=DOMAIN,
        modality="structured_json",
        source_type="external",
        permission_level=1,
        origin="investment_mock.json",
        version="v1",
        meta={"record_id": record_id, "record_type": kind},
    )


def _chunk(record_id: str, kind: str, content: str, source_ref: str, fields: dict[str, Any], confidence: float = 0.7) -> KbChunkRecord:
    return KbChunkRecord(
        chunk_id=f"chk:{kind}:{record_id}",
        doc_id=f"doc:{kind}:{record_id}",
        seq=0,
        content=content,
        domain_id=DOMAIN,
        source_type="external",
        confidence=confidence,
        permission_level=1,
        source_ref=source_ref,
        status="ready",
        tokens=token_text(content),
        meta={"record_id": record_id, "record_type": kind, "fields": fields},
    )


def build_ingest_package(db: Session, payload: dict[str, Any], source_name: str) -> IngestPackage:
    package = IngestPackage()
    for ent in payload.get("enterprises", []):
        eid = ent["ent_id"]
        profile = ent.get("profile", {})
        six_dim = profile.get("six_dim", {})
        content = (
            f"企业[{eid}] {ent.get('name', '')}（{', '.join(ent.get('aliases', []))}）："
            f"领域 {ent.get('track', '')}；阶段 {ent.get('stage', '')}；区域 {ent.get('region', '')}。"
            f"技术方向：{profile.get('tech_direction', {})}。融资：{profile.get('funding_stage', '')}。"
            f"研发需求：{profile.get('rd_needs', {})}。扩张动态：{profile.get('expansion_signals', {})}。"
            f"六维画像：{six_dim}。数据充分度：{profile.get('data_completeness', 0)}。"
        )
        package.documents.append(_doc(eid, "enterprises", ent.get("name", eid), ent.get("source_ref", "")))
        package.chunks.append(
            _chunk(eid, "enterprises", content, ent.get("source_ref", ""), {"six_dim": six_dim, "completeness": profile.get("data_completeness", 0)})
        )
    for res in payload.get("park_resources", []):
        rid = res["resource_id"]
        content = f"园区资源[{rid}｜{res.get('type', '')}] {res.get('name', '')}：{res.get('attributes', {})}；状态 {res.get('availability', '')}。"
        package.documents.append(_doc(rid, "park_resources", res.get("name", rid), f"园区资源库/{rid}"))
        package.chunks.append(_chunk(rid, "park_resources", content, f"园区资源库/{rid}", {"attributes": res.get("attributes", {})}, confidence=0.9))
    for pol in payload.get("policies", []):
        pid = pol["policy_id"]
        content = (
            f"政策[{pid}] {pol.get('title', '')}：适用 {pol.get('applicability', {})}；"
            f"发布机构 {pol.get('issuing_body', '')}；有效期至 {pol.get('valid_until', '')}。"
        )
        package.documents.append(_doc(pid, "policies", pol.get("title", pid), pol.get("source_ref", "")))
        package.chunks.append(_chunk(pid, "policies", content, pol.get("source_ref", ""), {"applicability": pol.get("applicability", {})}, confidence=0.85))
    return package


def write_structured_rows(db: Session, payload: dict[str, Any], source_name: str) -> int:
    count = 0
    for ent in payload.get("enterprises", []):
        row = db.get(InvEnterprise, ent["ent_id"]) or InvEnterprise(ent_id=ent["ent_id"])
        db.add(row)
        row.name = ent.get("name", "")
        row.aliases = ent.get("aliases", [])
        row.track = ent.get("track", "")
        row.stage = ent.get("stage", "")
        row.region = ent.get("region", "")
        row.unified_credit_code = ent.get("unified_credit_code", "")
        row.source_type = "external"
        row.source_ref = ent.get("source_ref", "")
        profile = ent.get("profile", {})
        prow = db.get(InvProfile, f"PROF-{ent['ent_id']}") or InvProfile(profile_id=f"PROF-{ent['ent_id']}")
        db.add(prow)
        prow.ent_id = ent["ent_id"]
        prow.tech_direction = profile.get("tech_direction", {})
        prow.funding_stage = profile.get("funding_stage", "")
        prow.rd_needs = profile.get("rd_needs", {})
        prow.expansion_signals = profile.get("expansion_signals", {})
        prow.six_dim = profile.get("six_dim", {})
        prow.data_completeness = profile.get("data_completeness", 0)
        prow.source_type = "external"
        prow.source_ref = ent.get("source_ref", "")
        count += 1
    for res in payload.get("park_resources", []):
        row = db.get(InvParkResource, res["resource_id"]) or InvParkResource(resource_id=res["resource_id"])
        db.add(row)
        row.type = res.get("type", "")
        row.name = res.get("name", "")
        row.attributes = res.get("attributes", {})
        row.availability = res.get("availability", "available")
        count += 1
    for pol in payload.get("policies", []):
        row = db.get(InvPolicy, pol["policy_id"]) or InvPolicy(policy_id=pol["policy_id"])
        db.add(row)
        row.title = pol.get("title", "")
        row.applicability = pol.get("applicability", {})
        row.issuing_body = pol.get("issuing_body", "")
        row.valid_until = pol.get("valid_until", "")
        row.source_type = "external"
        row.source_ref = pol.get("source_ref", "")
        count += 1
    db.commit()
    return count
