"""G-23 嵌入模型回归：更换嵌入后，公开草案集的要求条款是否仍能召回。

在同一演示库中依次重建向量（不是独立索引），比较混合检索的条款召回；
只使用公开政策，不发送个人信息。

用法（platform 目录）：
    ./.venv/Scripts/python.exe scripts/run_gov_embedding_eval.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT / "scripts"))
sys.path.insert(0, str(PLATFORM_ROOT))

import run_gov_model_eval as base  # noqa: E402
from sqlalchemy import select  # noqa: E402

import core.runtime as runtime  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.gov_office.schemas import PolicySearchIn  # noqa: E402
from app.gov_office.services import GovPolicyService  # noqa: E402
from domains.gov_office.models import GovPolicyDocument  # noqa: E402

EMBED_MODELS = [
    "text-embedding-v3",
    "qwen3.7-text-embedding-flash",
    "qwen3.7-text-embedding",
]
REPORT_DIR = base.REPORT_DIR


def searchable_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        case
        for case in cases
        if case["expected"].get("required_citation_ids")
        and case["input"].get("jurisdiction")
        and case["input"].get("as_of")
    ]


def write_report(payload: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"embedding-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 嵌入模型条款召回回归（G-23 部分）",
        "",
        f"- 运行时间（UTC）：{payload['run_at']}",
        f"- 样本：{payload['case_count']} 条有明确引用要求的公开草案样本",
        "- 方法：同一演示库依次重建向量，再跑混合检索；不是独立索引，也不是纯向量对比",
        "- 口径：开发者自检，不是正式选型",
        "",
        "| 嵌入模型 | 要求引用命中 | 要求版本命中 | 平均检索时延 |",
        "|---|---|---|---|",
    ]
    for model, summary in payload["summary"].items():
        lines.append(
            f"| {model} | {summary['citation_hit']}/{summary['total']} | "
            f"{summary['version_hit']}/{summary['total']} | {summary['avg_latency_ms']} ms |"
        )
    lines.extend(["", "## 未命中明细", "", "| case | 模型 | 要求引用 | 实际 top-k |", "|---|---|---|---|"])
    for case_id, model_rows in payload["by_case"].items():
        for model, row in model_rows.items():
            if row["citation_hit"]:
                continue
            actual = ", ".join(row["actual_citation_ids"]) or "无"
            lines.append(
                f"| {case_id} | {model} | {', '.join(row['required'])} | {actual} |"
            )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 混合检索同时含词面分数；本对比不是纯向量召回。",
            "- 同一库依次重建向量，不是独立索引；正式选型需要独立索引和完整回归。",
            "- 公开草案样本 12 条，未独立复核，不能外推通用召回率。",
        ]
    )
    md_path = REPORT_DIR / f"embedding-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


async def main_async() -> int:
    settings = get_settings()
    cases = searchable_cases(base.load_cases())
    by_case: dict[str, Any] = {case["case_id"]: {} for case in cases}
    summary: dict[str, Any] = {}
    db = SessionLocal()
    try:
        has_docs = db.execute(select(GovPolicyDocument.doc_id)).first() is not None
        if not has_docs:
            await base.prepare(db)
        samples = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(base.SAMPLE_DIR.glob("*.json"))
        ]
        service = GovPolicyService(db)
        for embed_model in EMBED_MODELS:
            settings.EMBED_MODEL = embed_model
            settings.EMBED_DIM = 1024
            runtime.reset_runtime()
            for sample in samples:
                await service.build_embeddings(
                    sample["document"]["doc_id"],
                    sample["version"]["version_label"],
                    base.sample_actor(),
                )
            hit = 0
            version_hit = 0
            latencies = []
            for case in cases:
                started = time.perf_counter()
                result = await service.search(
                    PolicySearchIn(
                        query=case["input"]["user_request"],
                        jurisdiction=case["input"]["jurisdiction"],
                        subject=case["input"].get("subject", ""),
                        as_of=case["input"]["as_of"],
                        top_k=6,
                    ),
                    2,
                )
                latency = round((time.perf_counter() - started) * 1000, 2)
                latencies.append(latency)
                actual_citations = [
                    item["clause"]["citation_id"] for item in result.get("items", [])
                ]
                actual_versions = [
                    item["version"]["version_label"] for item in result.get("items", [])
                ]
                required = case["expected"]["required_citation_ids"]
                required_versions = case["expected"].get("required_policy_versions", [])
                citation_ok = bool(set(required) & set(actual_citations))
                version_ok = all(version in actual_versions for version in required_versions)
                hit += int(citation_ok)
                version_hit += int(version_ok)
                by_case[case["case_id"]][embed_model] = {
                    "required": required,
                    "actual_citation_ids": actual_citations,
                    "citation_hit": citation_ok,
                    "version_hit": version_ok,
                    "latency_ms": latency,
                }
            summary[embed_model] = {
                "total": len(cases),
                "citation_hit": hit,
                "version_hit": version_hit,
                "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            }
    finally:
        db.close()

    payload = {
        "run_at": datetime.now(UTC).isoformat(),
        "case_count": len(cases),
        "models": EMBED_MODELS,
        "summary": summary,
        "by_case": by_case,
    }
    md_path = write_report(payload)
    print(json.dumps({"status": "PASS", "summary": summary, "report": str(md_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
