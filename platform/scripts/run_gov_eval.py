"""政务评测首轮运行器：完整方案 vs 朴素检索基线。

- 只读公开样本与冻结样本文件；结果写入 evaluation/reports/
- 运行环境为 testing（内存 SQLite、离线嵌入），不依赖外部模型
- 评测失败是数据，不改变退出码；初始化或读取失败才返回非零

用法（platform 目录）：
    ./.venv/Scripts/python.exe scripts/run_gov_eval.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLATFORM_ROOT.parent
EVAL_DIR = REPO_ROOT / "政务智能体" / "evaluation"
CASES_DIR = EVAL_DIR / "cases"
SAMPLE_DIR = REPO_ROOT / "政务智能体" / "samples" / "public"
REPORT_DIR = EVAL_DIR / "reports"

os.environ["APP_ENV"] = "testing"
sys.path.insert(0, str(PLATFORM_ROOT))
try:
    cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: S110 - 非交互控制台兜底
    pass

import jieba  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.db as app_db  # noqa: E402
import core.models  # noqa: E402,F401 - 注册全部 ORM 模型
import domains.gov_office.models  # noqa: E402,F401
from app import create_app  # noqa: E402
from app.db import Base  # noqa: E402
from core.admin.seed import seed_all  # noqa: E402


def load_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def citation_id(doc_id: str, version_label: str, clause_no: str) -> str:
    digest = hashlib.sha256(f"{doc_id}:{version_label}|{clause_no}".encode()).hexdigest()
    return f"CL-{digest[:32]}@{version_label}"


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败 {username}: {resp.status_code} {resp.text}")
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def prepare_policy(client: TestClient, kb: dict[str, str]) -> list[dict[str, Any]]:
    samples = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SAMPLE_DIR.glob("*.json"))
    ]
    for index, sample in enumerate(samples):
        resp = client.post(
            "/api/v1/gov/policies/import",
            json=sample,
            headers={**kb, "Idempotency-Key": f"eval-import-{index}"},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"导入失败：{resp.status_code} {resp.text}")

    doc_id = samples[0]["document"]["doc_id"]
    detail = client.get(f"/api/v1/gov/policies/{doc_id}", headers=kb).json()["data"]
    for relation in detail.get("relations", []):
        if relation.get("review_status") == "approved":
            continue
        reviewed = client.post(
            f"/api/v1/gov/policies/relations/{relation['relation_id']}/review",
            json={"action": "approve", "note": "评测初始化"},
            headers={**kb, "Idempotency-Key": f"eval-rel-{relation['relation_id']}"},
        )
        if reviewed.status_code != 200:
            raise RuntimeError(f"关系审核失败：{reviewed.status_code} {reviewed.text}")

    for index, sample in enumerate(samples):
        label = sample["version"]["version_label"]
        reviewed = client.post(
            f"/api/v1/gov/policies/{doc_id}/versions/{label}/review",
            json={"action": "approve", "note": "评测初始化"},
            headers={**kb, "Idempotency-Key": f"eval-review-{index}"},
        )
        if reviewed.status_code != 200:
            raise RuntimeError(f"版本审核失败：{reviewed.status_code} {reviewed.text}")
        published = client.post(
            f"/api/v1/gov/policies/{doc_id}/publish",
            headers={**kb, "Idempotency-Key": f"eval-publish-{index}"},
        )
        if published.status_code != 200:
            raise RuntimeError(f"发布失败：{published.status_code} {published.text}")
        embedded = client.post(
            f"/api/v1/gov/policies/{doc_id}/versions/{label}/embeddings",
            headers={**kb, "Idempotency-Key": f"eval-embed-{index}"},
        )
        if embedded.status_code != 200:
            raise RuntimeError(f"建向量失败：{embedded.status_code} {embedded.text}")
    return samples


def walk_values(obj: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(walk_values(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(walk_values(item, key))
    return found


def get_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def normalize(text: str) -> str:
    return "".join((text or "").split())


def evaluate(case: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
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

    checks = set(actual.get("checks", []))
    for code in expected.get("checks", []):
        if code not in checks:
            reasons.append(f"缺少检查项：{code}")

    if "http_status" in expected and actual.get("http_status") != expected["http_status"]:
        reasons.append(
            f"HTTP 期望 {expected['http_status']}，实际 {actual.get('http_status')}"
        )
    if "error_code" in expected and actual.get("error_code") != expected["error_code"]:
        reasons.append(
            f"错误码期望 {expected['error_code']}，实际 {actual.get('error_code')}"
        )
    for field, value in expected.get("field_values", {}).items():
        actual_value = get_path(actual.get("raw"), field)
        if actual_value != value:
            reasons.append(f"字段 {field} 期望 {value}，实际 {actual_value}")
    return {"passed": not reasons, "reasons": reasons}


def result_from_policy(data: dict[str, Any]) -> dict[str, Any]:
    claims = data.get("claims", [])
    clarifications = data.get("clarifications", [])
    text = " ".join(
        [
            data.get("summary", ""),
            *(item.get("text", "") for item in claims),
            *(item.get("question", "") for item in clarifications),
            *data.get("warnings", []),
        ]
    )
    citations = data.get("citations", [])
    return {
        "outcome": data.get("outcome"),
        "text": text,
        "versions": sorted(
            {item.get("version_label") for item in citations if item.get("version_label")}
        ),
        "citation_ids": sorted(
            {item.get("citation_id") for item in citations if item.get("citation_id")}
        ),
        "raw": data,
    }


def result_from_draft(data: dict[str, Any]) -> dict[str, Any]:
    draft = data.get("draft", {})
    status = draft.get("status")
    outcome = {"pending_review": "review", "needs_revision": "insufficient"}.get(
        status, status
    )
    result = draft.get("result") or {}
    sections = draft.get("sections") or result.get("sections") or []
    checks = draft.get("checks") or []
    texts = [
        *(item.get("heading", "") for item in sections if isinstance(item, dict)),
        *(item.get("content", "") for item in sections if isinstance(item, dict)),
        *(item.get("message", "") for item in checks if isinstance(item, dict)),
        result.get("summary", ""),
        result.get("public_reply", ""),
        *(item.get("text", "") for item in result.get("key_points", [])),
        *(item.get("text", "") for item in result.get("tasks", [])),
    ]
    return {
        "outcome": outcome,
        "text": " ".join(item for item in texts if item),
        "checks": sorted(
            {item.get("code") for item in checks if isinstance(item, dict) and item.get("code")}
        ),
        "versions": sorted(
            {item for item in walk_values(data, "version_label") if isinstance(item, str)}
        ),
        "citation_ids": sorted(
            {item for item in walk_values(data, "citation_id") if isinstance(item, str)}
        ),
        "raw": data,
    }


def run_case(client: TestClient, headers: dict[str, str], case: dict[str, Any]) -> dict[str, Any]:
    mode = case["task_mode"]
    inp = case["input"]
    if mode == "policy_interpretation":
        body = {
            "question": inp["user_request"],
            "jurisdiction": inp.get("jurisdiction"),
            "as_of": inp.get("as_of"),
            "top_k": 6,
        }
        resp = client.post("/api/v1/gov/policies/interpret", json=body, headers=headers)
        if resp.status_code >= 400:
            payload = resp.json()
            return {
                "outcome": "block",
                "http_status": resp.status_code,
                "error_code": payload.get("code"),
                "raw": payload,
            }
        return result_from_policy(resp.json()["data"])

    if mode == "official_draft":
        resp = client.post(
            "/api/v1/gov/documents/drafts",
            json=inp.get("payload", {}),
            headers={**headers, "Idempotency-Key": f"eval-{case['case_id']}"},
        )
        if resp.status_code >= 400:
            payload = resp.json()
            return {
                "outcome": "block",
                "http_status": resp.status_code,
                "error_code": payload.get("code"),
                "raw": payload,
            }
        return result_from_draft(resp.json()["data"])

    if mode in {"content_writing", "summarization", "consultation_draft"}:
        payload = dict(inp.get("payload", {}))
        payload["request"] = inp["user_request"]
        resp = client.post(
            "/api/v1/gov/writing/drafts",
            json=payload,
            headers={**headers, "Idempotency-Key": f"eval-{case['case_id']}"},
        )
        if resp.status_code >= 400:
            body = resp.json()
            return {
                "outcome": "block",
                "http_status": resp.status_code,
                "error_code": body.get("code"),
                "raw": body,
            }
        return result_from_draft(resp.json()["data"])

    if mode == "security_routing":
        payload = inp.get("payload", {})
        resp = client.post(
            payload["endpoint"],
            json=payload.get("body", {}),
            headers=headers,
        )
        body = resp.json()
        return {
            "outcome": "block" if resp.status_code == 400 else "answer",
            "http_status": resp.status_code,
            "error_code": body.get("code"),
            "text": body.get("message", ""),
            "raw": body,
        }

    raise RuntimeError(f"未知 task_mode: {mode}")


def naive_policy(case: dict[str, Any], clauses: list[dict[str, Any]]) -> dict[str, Any]:
    query = case["input"]["user_request"]
    tokens = [
        token.strip()
        for token in jieba.lcut_for_search(query)
        if len(token.strip()) >= 2
    ]
    scored = []
    for clause in clauses:
        haystack = f"{clause['title']} {clause['content']}"
        matched = [token for token in tokens if token in haystack]
        coverage = len(matched) / len(tokens) if tokens else 0.0
        if coverage < 0.34:
            continue
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


def load_clauses(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for payload in samples:
        doc = payload["document"]
        label = payload["version"]["version_label"]
        for clause in payload["clauses"]:
            rows.append(
                {
                    "title": doc["title"],
                    "content": clause["content"],
                    "version_label": label,
                    "citation_id": citation_id(doc["doc_id"], label, clause["clause_no"]),
                }
            )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for row in rows if row["passed"])
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_mode[row["task_mode"]].append(row)
    modes = {}
    for mode, items in sorted(by_mode.items()):
        modes[mode] = {
            "passed": sum(1 for item in items if item["passed"]),
            "total": len(items),
        }
    high = [row for row in rows if row["risk"] == "high"]
    return {
        "passed": passed,
        "total": total,
        "high_risk": {
            "passed": sum(1 for row in high if row["passed"]),
            "total": len(high),
        },
        "by_mode": modes,
    }


def write_reports(
    cases: list[dict[str, Any]],
    full_results: list[dict[str, Any]],
    naive_results: list[dict[str, Any]],
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload: dict[str, Any] = {
        "run_at": datetime.now(UTC).isoformat(),
        "environment": "testing / sqlite in-memory / offline embedding",
        "model_arm": "not_run: G-23 blocked (no real model credentials)",
        "cases": len(cases),
        "full": {"summary": summarize(full_results), "results": full_results},
        "naive": {
            "summary": summarize(naive_results),
            "results": naive_results,
            "scope": "policy_interpretation only; ignores version, as_of and review status",
        },
    }
    json_path = REPORT_DIR / f"run-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 政务评测首轮结果（v1 校准轮）",
        "",
        f"- 运行时间（UTC）：{payload['run_at']}",
        "- 环境：testing / 内存 SQLite / 离线嵌入",
        "- 样本：公开政策测试集 15 条 + 合成开发集 12 条，全部 `label_status=draft`",
        "- 模型通道：未运行（G-23 缺真实模型凭证与人工金标）",
        "- 重要：本轮使用测试集定位问题，测试集已被使用，后续不得再称独立测试；最终结论需新冻结集或独立复测。",
        "",
        "## 汇总",
        "",
        "| 方案 | 范围 | 通过/分母 | 高风险通过/分母 |",
        "|---|---|---|---|",
    ]
    for name, data, scope in (
        ("完整方案", payload["full"], "test+dev"),
        ("朴素检索", payload["naive"], "test only"),
    ):
        s = data["summary"]
        lines.append(
            f"| {name} | {scope} | {s['passed']}/{s['total']} | "
            f"{s['high_risk']['passed']}/{s['high_risk']['total']} |"
        )
    lines.extend(["", "## 分桶", "", "| 任务 | 完整方案 |", "|---|---|"])
    for mode, stat in payload["full"]["summary"]["by_mode"].items():
        lines.append(f"| {mode} | {stat['passed']}/{stat['total']} |")
    lines.extend(["", "## 失败明细", "", "| 方案 | case | 期望 | 实际 | 原因 |", "|---|---|---|---|---|"])
    for name, data in (("完整方案", full_results), ("朴素检索", naive_results)):
        for row in data:
            if row["passed"]:
                continue
            lines.append(
                f"| {name} | {row['case_id']} | {row['expected_outcome']} | "
                f"{row.get('actual_outcome')} | {'；'.join(row['reasons'])} |"
            )
    lines.extend(
        [
            "",
            "## 解释与限制",
            "",
            "- 分子分母均来自本报告 JSON 原始记录，可重算；未删除失败样本。",
            "- 朴素检索只做词面覆盖打分，故意不实现版本时效、审核发布、澄清和安全分流，用于说明这些机制的作用。",
            "- 公开政策仅一组文件两个版本，不能外推为通用政策准确率；合成样本不代表机关真实写作质量。",
            "- 本轮没有真实大模型、没有独立人工复核、没有真实用户，不能写成政务业务准确率或专家结论。",
        ]
    )
    md_path = REPORT_DIR / f"run-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


def main() -> int:
    cases = load_cases()
    Base.metadata.create_all(bind=app_db.engine)
    session = app_db.SessionLocal()
    seed_all(session)
    session.commit()

    application = create_app("testing")
    with TestClient(application) as client:
        kb = login(client, "kbadmin", "Kb@12345")
        expert = login(client, "expert", "Expert@12345")
        samples = prepare_policy(client, kb)
        clauses = load_clauses(samples)

        full_results = []
        naive_results = []
        for case in cases:
            actual = run_case(client, expert, case)
            verdict = evaluate(case, actual)
            full_results.append(
                {
                    "case_id": case["case_id"],
                    "split": case["split"],
                    "task_mode": case["task_mode"],
                    "risk": case["risk"],
                    "expected_outcome": case["expected"]["outcome"],
                    "actual_outcome": actual.get("outcome"),
                    "actual_text": (actual.get("text") or "")[:800],
                    "actual_checks": actual.get("checks", []),
                    "actual_versions": actual.get("versions", []),
                    "actual_citation_ids": actual.get("citation_ids", []),
                    **verdict,
                }
            )
            if case["split"] == "test" and case["task_mode"] == "policy_interpretation":
                actual = naive_policy(case, clauses)
                verdict = evaluate(case, actual)
                naive_results.append(
                    {
                        "case_id": case["case_id"],
                        "split": case["split"],
                        "task_mode": case["task_mode"],
                        "risk": case["risk"],
                        "expected_outcome": case["expected"]["outcome"],
                        "actual_outcome": actual.get("outcome"),
                        "actual_text": (actual.get("text") or "")[:800],
                        "actual_versions": actual.get("versions", []),
                        "actual_citation_ids": actual.get("citation_ids", []),
                        **verdict,
                    }
                )

    session.close()
    md_path = write_reports(cases, full_results, naive_results)
    full_summary = summarize(full_results)
    naive_summary = summarize(naive_results)
    print(
        json.dumps(
            {
                "status": "PASS",
                "full": full_summary,
                "naive": naive_summary,
                "report": str(md_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
