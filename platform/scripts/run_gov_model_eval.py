"""G-23/G-26 通用模型初筛：真实百炼模型 + 公开政策草案集。

只使用公开政策条款和合成/公开问题，不发送个人信息或内部材料。
结果仅代表公开样本草案集上的开发者自检，不是正式选型或政务准确率。

用法（platform 目录，需在 .env 配置百炼 API Key 与模型）：
    ./.venv/Scripts/python.exe scripts/run_gov_model_eval.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLATFORM_ROOT.parent
CASES_PATH = REPO_ROOT / "政务智能体" / "evaluation" / "cases" / "public-test-v1.jsonl"
SAMPLE_DIR = REPO_ROOT / "政务智能体" / "samples" / "public"
REPORT_DIR = REPO_ROOT / "政务智能体" / "evaluation" / "reports"
DB_PATH = REPO_ROOT / "_tmp" / "gov_model_eval.db"

os.environ["APP_ENV"] = "development"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
os.environ["DEMO_MODE"] = "live"
os.environ["METRICS_ENABLED"] = "false"
os.environ["MCP_ENABLED"] = "false"
os.environ["RATE_LIMIT_DEFAULT"] = "1000000/hour"
os.environ["CACHE_BACKEND"] = "memory"
sys.path.insert(0, str(PLATFORM_ROOT))

import jieba  # noqa: E402

import core.models  # noqa: E402,F401 - 注册全部 ORM 模型
import domains.gov_office.models  # noqa: E402,F401
from adapters.model_gateway import OpenAiCompatGateway  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.gov_office.schemas import (  # noqa: E402
    PolicyImportIn,
    PolicyInterpretIn,
    PolicyReviewIn,
    PolicySearchIn,
)
from app.gov_office.services import GovPolicyService  # noqa: E402
from core.abstraction.dtos import Actor  # noqa: E402
from core.abstraction.enums import Roles  # noqa: E402
from core.admin.seed import seed_all  # noqa: E402

MODELS = ["qwen3.8-max", "qwen3.8-flash", "deepseek-v4.1-flash"]


def sample_actor() -> Actor:
    return Actor(
        user_id="u-kbadmin",
        role_id=Roles.KB_ADMIN,
        permission_max=2,
        domain_scope=["chemical", "investment", "gov_office"],
    )


def load_cases() -> list[dict[str, Any]]:
    rows = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


async def prepare(db) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    service = GovPolicyService(db)
    actor = sample_actor()
    samples = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SAMPLE_DIR.glob("*.json"))
    ]
    for index, raw in enumerate(samples):
        payload = PolicyImportIn.model_validate(raw)
        service.import_policy(payload, actor, f"model-eval-import-{index}")
        service.review_version(
            payload.document.doc_id,
            payload.version.version_label,
            PolicyReviewIn(action="approve", note="模型初筛初始化"),
            actor,
        )
        service.publish_document(payload.document.doc_id, actor)
        await service.build_embeddings(
            payload.document.doc_id, payload.version.version_label, actor
        )
    doc_id = samples[0]["document"]["doc_id"]
    detail = service.get_version(doc_id, samples[0]["version"]["version_label"])
    for relation in detail.get("relations", []):
        if relation.get("review_status") != "approved":
            service.review_relation(
                relation["relation_id"],
                PolicyReviewIn(action="approve", note="模型初筛初始化"),
                actor,
            )
    db.commit()
    return samples, load_clauses(samples)


def load_clauses(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for payload in samples:
        doc = payload["document"]
        label = payload["version"]["version_label"]
        for clause in payload["clauses"]:
            digest = hashlib.sha256(
                f"{doc['doc_id']}:{label}|{clause['clause_no']}".encode()
            ).hexdigest()
            rows.append(
                {
                    "title": doc["title"],
                    "content": clause["content"],
                    "version_label": label,
                    "citation_id": f"CL-{digest[:32]}@{label}",
                }
            )
    return rows


def normalize(text: str) -> str:
    return "".join((text or "").split())


def evaluate(case: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    expected = case["expected"]
    reasons: list[str] = []
    if actual.get("outcome") != expected["outcome"]:
        reasons.append(f"outcome 期望 {expected['outcome']}，实际 {actual.get('outcome')}")
    text = normalize(actual.get("text", ""))
    for needle in expected.get("must_include", []):
        if normalize(needle) not in text:
            reasons.append(f"缺少必需内容：{needle}")
    for needle in expected.get("must_not_include", []):
        if normalize(needle) in text:
            reasons.append(f"出现不应有的内容：{needle}")
    versions = set(actual.get("versions", []))
    for version in expected.get("required_policy_versions", []):
        if version not in versions:
            reasons.append(f"缺少版本引用：{version}")
    citations = set(actual.get("citation_ids", []))
    for cid in expected.get("required_citation_ids", []):
        if cid not in citations:
            reasons.append(f"缺少引用：{cid}")
    return reasons


def extractive_result(data: dict[str, Any]) -> dict[str, Any]:
    claims = data.get("claims", [])
    clarifications = data.get("clarifications", [])
    return {
        "outcome": data.get("outcome"),
        "text": " ".join(
            [
                data.get("summary", ""),
                *(item.get("text", "") for item in claims),
                *(item.get("question", "") for item in clarifications),
                *data.get("warnings", []),
            ]
        ),
        "versions": sorted(
            {
                item.get("version_label")
                for item in data.get("citations", [])
                if item.get("version_label")
            }
        ),
        "citation_ids": sorted(
            {
                item.get("citation_id")
                for item in data.get("citations", [])
                if item.get("citation_id")
            }
        ),
    }


def naive_result(case: dict[str, Any], clauses: list[dict[str, Any]]) -> dict[str, Any]:
    query = case["input"]["user_request"]
    tokens = [
        token.strip() for token in jieba.lcut_for_search(query) if len(token.strip()) >= 2
    ]
    scored = []
    for clause in clauses:
        haystack = f"{clause['title']} {clause['content']}"
        matched = [token for token in tokens if token in haystack]
        coverage = len(matched) / len(tokens) if tokens else 0.0
        if coverage >= 0.34:
            scored.append((coverage, clause))
    scored.sort(key=lambda item: -item[0])
    top = [item[1] for item in scored[:3]]
    if not top:
        return {"outcome": "insufficient", "text": "", "versions": [], "citation_ids": []}
    return {
        "outcome": "answer",
        "text": " ".join(item["content"] for item in top),
        "versions": sorted({item["version_label"] for item in top}),
        "citation_ids": sorted({item["citation_id"] for item in top}),
    }


def build_context(items: list[dict[str, Any]]) -> tuple[str, dict[str, dict[str, Any]]]:
    lines = []
    mapping: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, start=1):
        alias = f"C{index}"
        clause = item["clause"]
        mapping[alias] = item
        lines.append(
            f"[{alias}] 《{item['document']['title']}》{clause['clause_no']} "
            f"（版本：{item['version']['version_label']}）\n{clause['content']}"
        )
    return "\n\n".join(lines), mapping


def prompt_for(case: dict[str, Any], context: str) -> list[dict[str, str]]:
    inp = case["input"]
    system = (
        "你是政务政策辅助助手。只能依据给出的已审核条款回答，不得使用外部知识。"
        "如果条款不足以回答，输出 insufficient；不要猜测。"
        "必须输出 JSON，字段为 outcome、answer、claims；claims 中每条包含 text 和 citation_ids。"
        "citation_ids 只能使用给出的 C1、C2 等别名。"
    )
    user = (
        f"问题：{inp['user_request']}\n"
        f"地区：{inp.get('jurisdiction') or '未提供'}\n"
        f"事项日期：{inp.get('as_of') or '未提供'}\n\n"
        f"条款：\n{context}\n\n"
        "请输出 JSON：{\"outcome\":\"answer 或 insufficient\",\"answer\":\"...\","
        "\"claims\":[{\"text\":\"...\",\"citation_ids\":[\"C1\"]}]}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_model_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


async def run_model_case(
    gateway: OpenAiCompatGateway, service: GovPolicyService, case: dict[str, Any]
) -> dict[str, Any]:
    if not case["input"].get("jurisdiction") or not case["input"].get("as_of"):
        return {"outcome": "clarify", "text": "缺少地区或事项时间", "versions": [], "citation_ids": []}
    search = await service.search(
        PolicySearchIn(
            query=case["input"]["user_request"],
            jurisdiction=case["input"]["jurisdiction"],
            subject=case["input"].get("subject", ""),
            as_of=case["input"]["as_of"],
            top_k=6,
        ),
        2,
    )
    items = search["items"]
    if not items:
        return {"outcome": "insufficient", "text": "", "versions": [], "citation_ids": []}
    context, mapping = build_context(items)
    started = time.perf_counter()
    try:
        result = await gateway.chat(prompt_for(case, context), json_mode=True, timeout_s=120)
    except Exception:
        try:
            result = await gateway.chat(prompt_for(case, context), json_mode=False, timeout_s=120)
        except Exception as exc:
            return {
                "outcome": "error",
                "text": "",
                "versions": [],
                "citation_ids": [],
                "error": str(exc)[:200],
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
    latency = round((time.perf_counter() - started) * 1000, 2)
    parsed = parse_model_json(result.content)
    if parsed is None:
        return {
            "outcome": "invalid_json",
            "text": result.content[:800],
            "versions": [],
            "citation_ids": [],
            "latency_ms": latency,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        }
    aliases = set()
    for claim in parsed.get("claims", []):
        aliases.update(claim.get("citation_ids", []))
    mapped_items = [mapping[alias] for alias in aliases if alias in mapping]
    return {
        "outcome": parsed.get("outcome", "invalid"),
        "text": " ".join(
            [parsed.get("answer", "")]
            + [claim.get("text", "") for claim in parsed.get("claims", [])]
        ),
        "versions": sorted(
            {item["version"]["version_label"] for item in mapped_items}
        ),
        "citation_ids": sorted(
            {item["clause"]["citation_id"] for item in mapped_items}
        ),
        "latency_ms": latency,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "raw_content": result.content[:1200],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for row in rows if not row["reasons"])
    latencies = [row["actual"].get("latency_ms", 0) for row in rows if row["actual"].get("latency_ms")]
    tokens_in = sum(row["actual"].get("tokens_in", 0) for row in rows)
    tokens_out = sum(row["actual"].get("tokens_out", 0) for row in rows)
    return {
        "passed": passed,
        "total": len(rows),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def write_report(payload: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = REPORT_DIR / f"model-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 政务公开样本模型初筛（G-23 部分）",
        "",
        f"- 运行时间（UTC）：{payload['run_at']}",
        "- 数据：公开政策测试集 15 条，全部 `label_status=draft`",
        "- 发送内容：公开政策条款 + 公开/合成问题；不含个人信息或内部材料",
        "- 候选：qwen3.8-max、qwen3.8-flash、deepseek-v4.1-flash；嵌入 text-embedding-v3",
        "- 结论口径：公开样本开发者自检，不是正式选型、政务准确率或采购结论",
        "",
        "## 汇总",
        "",
        "| 方案 | 通过/分母 | 平均时延 | 最大时延 | 输入 tokens | 输出 tokens |",
        "|---|---|---|---|---|---|",
    ]
    for name, summary in payload["arms"].items():
        lines.append(
            f"| {name} | {summary['passed']}/{summary['total']} | "
            f"{summary['avg_latency_ms']} ms | {summary['max_latency_ms']} ms | "
            f"{summary['tokens_in']} | {summary['tokens_out']} |"
        )
    lines.extend(
        [
            "",
            "## 逐条结果",
            "",
            "| case | 期望 | 离线抽取 | 朴素检索 | "
            + " | ".join(payload["models"])
            + " |",
            "|---|---|---|---|" + "---|" * len(payload["models"]),
        ]
    )
    by_case = payload["by_case"]
    for case in payload["cases"]:
        cid = case["case_id"]
        row = by_case[cid]
        cells = [
            "PASS" if not row["extractive"]["reasons"] else "FAIL",
            "PASS" if not row["naive"]["reasons"] else "FAIL",
        ]
        for model in payload["models"]:
            cells.append("PASS" if not row["models"][model]["reasons"] else "FAIL")
        lines.append(
            f"| {cid} | {case['expected']['outcome']} | " + " | ".join(cells) + " |"
        )
    lines.extend(
        [
            "",
            "## 失败明细",
            "",
            "| 方案 | case | 原因 |",
            "|---|---|---|",
        ]
    )
    for case in payload["cases"]:
        cid = case["case_id"]
        row = by_case[cid]
        for name, actual in (
            ("离线抽取", row["extractive"]),
            ("朴素检索", row["naive"]),
            *[(model, row["models"][model]) for model in payload["models"]],
        ):
            if actual["reasons"]:
                lines.append(f"| {name} | {cid} | {'；'.join(actual['reasons'])} |")
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 15 条草案样本由开发者自检，没有独立第二人复核，不能称为人工金标。",
            "- 只比较公开政策问答；公文、文稿、咨询和安全分流未进入本轮模型对比。",
            "- 未做私有化部署、目标政务云时延和金额成本折算。",
            "- 重复一致性与支持性门禁见 repeat-*.json 和 support-ablation-*.json；"
            "重复只覆盖 6 条，不是完整 15 条。",
            "- must_include/must_not_include 为字面匹配，否定句和同义表述可能误伤；"
            "本轮 GOV-PUB-006（模型回答“不收取检索、复制、邮寄等成本费用”，"
            "仍含禁用短语）与 GOV-PUB-014（澄清用“地区”而非“政策地区”）需人工复核，"
            "自动分未调整。",
            "- 模型输出受提示词影响；同一模型重复运行可能存在波动。",
            "- 不得把本报告写成“最佳模型”或“已完成选型”。",
        ]
    )
    md_path = REPORT_DIR / f"model-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


async def main_async() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_all(db)
        db.commit()
        _, clauses = await prepare(db)
    finally:
        db.close()

    cases = load_cases()
    settings = get_settings()
    service_db = SessionLocal()
    service = GovPolicyService(service_db)
    try:
        by_case: dict[str, Any] = {}
        for case in cases:
            extractive_data = await service.interpret(
                PolicyInterpretIn(
                    question=case["input"]["user_request"],
                    jurisdiction=case["input"].get("jurisdiction"),
                    as_of=case["input"].get("as_of"),
                    top_k=6,
                ),
                2,
            )
            extractive = extractive_result(extractive_data)
            naive = naive_result(case, clauses)
            by_case[case["case_id"]] = {
                "extractive": {
                    "actual": extractive,
                    "reasons": evaluate(case, extractive),
                },
                "naive": {"actual": naive, "reasons": evaluate(case, naive)},
                "models": {},
            }
    finally:
        service_db.close()

    for model in MODELS:
        gateway = OpenAiCompatGateway(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=model,
            embed_model=settings.EMBED_MODEL,
            timeout_s=120.0,
        )
        db = SessionLocal()
        service = GovPolicyService(db)
        try:
            for case in cases:
                actual = await run_model_case(gateway, service, case)
                by_case[case["case_id"]]["models"][model] = {
                    "actual": actual,
                    "reasons": evaluate(case, actual),
                }
        finally:
            db.close()
        await gateway.client.aclose()

    arms: dict[str, Any] = {
        "离线抽取": summarize(
            [{"actual": row["extractive"]["actual"], "reasons": row["extractive"]["reasons"]} for row in by_case.values()]
        ),
        "朴素检索": summarize(
            [{"actual": row["naive"]["actual"], "reasons": row["naive"]["reasons"]} for row in by_case.values()]
        ),
    }
    for model in MODELS:
        arms[model] = summarize(
            [
                {"actual": row["models"][model]["actual"], "reasons": row["models"][model]["reasons"]}
                for row in by_case.values()
            ]
        )

    payload = {
        "run_at": datetime.now(UTC).isoformat(),
        "models": MODELS,
        "embedding_model": settings.EMBED_MODEL,
        "cases": cases,
        "by_case": by_case,
        "arms": arms,
    }
    md_path = write_report(payload)
    print(
        json.dumps(
            {
                "status": "PASS",
                "arms": arms,
                "report": str(md_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
