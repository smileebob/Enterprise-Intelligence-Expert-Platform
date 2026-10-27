"""G-23 重复一致性抽查：同一模型对同一批样本运行三次。

只跑 6 条高风险/边界样本，用于检查结果稳定性和时延离散度；
不是完整 15 条的三次重复，也不构成正式选型。

用法（platform 目录）：
    ./.venv/Scripts/python.exe scripts/run_gov_model_repeat.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT / "scripts"))
sys.path.insert(0, str(PLATFORM_ROOT))

import run_gov_model_eval as base  # noqa: E402

from adapters.model_gateway import OpenAiCompatGateway  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.gov_office.services import GovPolicyService  # noqa: E402
from core.admin.seed import seed_all  # noqa: E402

CASE_IDS = [
    "GOV-PUB-001",
    "GOV-PUB-002",
    "GOV-PUB-006",
    "GOV-PUB-008",
    "GOV-PUB-010",
    "GOV-PUB-014",
]
REPEATS = 3
REPORT_DIR = base.REPORT_DIR


def summarize_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    all_pass = 0
    any_pass = 0
    stable = 0
    for case_rows in by_case.values():
        passes = [not item["reasons"] for item in case_rows]
        if all(passes):
            all_pass += 1
        if any(passes):
            any_pass += 1
        outcomes = {item["actual"].get("outcome") for item in case_rows}
        if len(outcomes) == 1:
            stable += 1
    latencies = [row["actual"].get("latency_ms", 0) for row in rows if row["actual"].get("latency_ms")]
    return {
        "cases": len(by_case),
        "all_three_pass": all_pass,
        "any_pass": any_pass,
        "stable_outcome": stable,
        "median_latency_ms": round(statistics.median(latencies), 2) if latencies else 0,
        "min_latency_ms": round(min(latencies), 2) if latencies else 0,
        "max_latency_ms": round(max(latencies), 2) if latencies else 0,
        "pass_by_run": [
            sum(1 for row in rows if row["run"] == run and not row["reasons"])
            for run in range(1, REPEATS + 1)
        ],
    }


def write_report(payload: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"repeat-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 模型重复一致性抽查（G-23 部分）",
        "",
        f"- 运行时间（UTC）：{payload['run_at']}",
        f"- 样本：公开草案集 {len(CASE_IDS)} 条，每个模型重复 {REPEATS} 次",
        "- 口径：开发者自检，不是完整测试集，不是正式选型",
        "",
        "| 模型 | 三次全过 | 至少一次通过 | 结果稳定 | 中位时延 | 最小值 | 最大值 | 各轮通过 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model, summary in payload["summary"].items():
        lines.append(
            f"| {model} | {summary['all_three_pass']}/{summary['cases']} | "
            f"{summary['any_pass']}/{summary['cases']} | {summary['stable_outcome']}/{summary['cases']} | "
            f"{summary['median_latency_ms']} ms | {summary['min_latency_ms']} ms | "
            f"{summary['max_latency_ms']} ms | {summary['pass_by_run']} |"
        )
    lines.extend(["", "## 逐条", "", "| case | 模型 | 三次结果 | 三次 outcome |", "|---|---|---|---|"])
    for case_id, case_rows in payload["by_case"].items():
        for model, model_rows in case_rows.items():
            results = ["PASS" if not row["reasons"] else "FAIL" for row in model_rows]
            outcomes = [row["actual"].get("outcome") for row in model_rows]
            lines.append(f"| {case_id} | {model} | {' / '.join(results)} | {outcomes} |")
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 只抽查 6 条，不是完整 15 条；不能据此计算稳定准确率。",
            "- 模型输出受提示词和采样影响；本轮 temperature=0。",
            "- 没有人工复核，自动判据仍可能对否定句和同义表述误伤。",
        ]
    )
    md_path = REPORT_DIR / f"repeat-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


async def main_async() -> int:
    base.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    base.DB_PATH.unlink(missing_ok=True)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_all(db)
        db.commit()
        await base.prepare(db)
    finally:
        db.close()

    cases = [case for case in base.load_cases() if case["case_id"] in CASE_IDS]
    settings = get_settings()
    rows: list[dict[str, Any]] = []
    by_case: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for model in base.MODELS:
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
            for run in range(1, REPEATS + 1):
                for case in cases:
                    actual = await base.run_model_case(gateway, service, case)
                    row = {
                        "case_id": case["case_id"],
                        "model": model,
                        "run": run,
                        "actual": actual,
                        "reasons": base.evaluate(case, actual),
                    }
                    rows.append(row)
                    by_case.setdefault(case["case_id"], {}).setdefault(model, []).append(row)
        finally:
            db.close()
        await gateway.client.aclose()

    summary = {model: summarize_model([row for row in rows if row["model"] == model]) for model in base.MODELS}
    payload = {
        "run_at": datetime.now(UTC).isoformat(),
        "models": base.MODELS,
        "case_ids": CASE_IDS,
        "repeats": REPEATS,
        "summary": summary,
        "by_case": by_case,
    }
    md_path = write_report(payload)
    print(json.dumps({"status": "PASS", "summary": summary, "report": str(md_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
