"""G-26 支持性检查消融：对已有模型输出做离线支持性判定。

读取最近一次 model-*.json，不重新调用模型；对每条 claim 用引用条款做
确定性支持检查，比较“原样输出”和“仅接受有支持内容的输出”。

用法（platform 目录）：
    ./.venv/Scripts/python.exe scripts/run_gov_support_ablation.py
"""

from __future__ import annotations

import asyncio
import glob
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT / "scripts"))
sys.path.insert(0, str(PLATFORM_ROOT))

import run_gov_model_eval as base  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.gov_office.schemas import PolicySupportIn  # noqa: E402
from app.gov_office.services import GovPolicyService  # noqa: E402

REPORT_DIR = base.REPORT_DIR


def latest_model_report() -> tuple[Path, dict[str, Any]]:
    paths = sorted(glob.glob(str(REPORT_DIR / "model-*.json")))
    if not paths:
        raise FileNotFoundError("没有 model-*.json，请先运行 run_gov_model_eval.py")
    path = Path(paths[-1])
    return path, json.loads(path.read_text(encoding="utf-8"))


def parse_claims(raw_content: str) -> list[dict[str, Any]]:
    text = (raw_content or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [claim for claim in data.get("claims", []) if claim.get("text")]


async def assess_case(
    service: GovPolicyService, actual: dict[str, Any]
) -> dict[str, Any]:
    claims = parse_claims(actual.get("raw_content", ""))
    citation_ids = actual.get("citation_ids", [])
    if not claims or not citation_ids:
        return {
            "claims": 0,
            "unsupported": 0,
            "statuses": [],
            "gated_outcome": actual.get("outcome"),
            "gated_text": actual.get("text", ""),
        }
    statuses = []
    unsupported = 0
    for claim in claims:
        try:
            result = await service.check_support(
                PolicySupportIn(
                    claim=claim["text"],
                    citation_ids=citation_ids,
                    use_semantic=False,
                ),
                2,
            )
            status = result["support_status"]
        except Exception:
            status = "error"
        statuses.append(status)
        if status in {"insufficient", "error"}:
            unsupported += 1
    gated_outcome = "insufficient" if unsupported else actual.get("outcome")
    gated_text = "" if unsupported else actual.get("text", "")
    return {
        "claims": len(claims),
        "unsupported": unsupported,
        "statuses": statuses,
        "gated_outcome": gated_outcome,
        "gated_text": gated_text,
    }


def write_report(payload: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"support-ablation-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 支持性检查消融（G-26 部分）",
        "",
        f"- 运行时间（UTC）：{payload['run_at']}",
        f"- 输入：`{Path(payload['source']).name}`",
        "- 方法：对模型 claims 做确定性支持检查；`direct/partial` 接受，`insufficient/error` 降级",
        "- 口径：离线开发者自检，不是人工复核，不是正式选型",
        "",
        "| 模型 | 原样通过 | 加支持检查通过 | 含不支持 claim 的样本 |",
        "|---|---|---|---|",
    ]
    for model, data in payload["summary"].items():
        lines.append(
            f"| {model} | {data['original_pass']}/{data['total']} | "
            f"{data['gated_pass']}/{data['total']} | {data['unsupported_cases']} |"
        )
    lines.extend(
        [
            "",
            "## 逐条",
            "",
            "| case | 模型 | 原样 | claim 数 | 不支持 | 支持状态 | 加检查后 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for case in payload["cases"]:
        cid = case["case_id"]
        for model in payload["models"]:
            row = payload["by_case"][cid]["models"][model]
            lines.append(
                f"| {cid} | {model} | {'PASS' if not row['original_reasons'] else 'FAIL'} | "
                f"{row['claims']} | {row['unsupported']} | {row['statuses']} | "
                f"{'PASS' if not row['gated_reasons'] else 'FAIL'} |"
            )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 支持检查是词面覆盖 + 否定风险规则，`partial` 仍可能是弱支持；不是语义证明。",
            "- 模型回答中的 claim 可能被截断或 JSON 解析失败，按不支持处理。",
            "- 该消融不重新调用模型，只检验已有输出能否被引用条款支撑。",
        ]
    )
    md_path = REPORT_DIR / f"support-ablation-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


async def main_async() -> int:
    source_path, source = latest_model_report()
    db = SessionLocal()
    service = GovPolicyService(db)
    by_case: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    try:
        for case in source["cases"]:
            cid = case["case_id"]
            by_case[cid] = {"models": {}}
            for model in source["models"]:
                original = source["by_case"][cid]["models"][model]
                assessed = await assess_case(service, original["actual"])
                original_reasons = base.evaluate(case, original["actual"])
                gated = {
                    "outcome": assessed["gated_outcome"],
                    "text": assessed["gated_text"],
                    "versions": original["actual"].get("versions", []),
                    "citation_ids": original["actual"].get("citation_ids", []),
                }
                gated_reasons = base.evaluate(case, gated)
                by_case[cid]["models"][model] = {
                    **assessed,
                    "original_reasons": original_reasons,
                    "gated_reasons": gated_reasons,
                }
        for model in source["models"]:
            rows = [by_case[cid]["models"][model] for cid in by_case]
            summary[model] = {
                "total": len(rows),
                "original_pass": sum(1 for row in rows if not row["original_reasons"]),
                "gated_pass": sum(1 for row in rows if not row["gated_reasons"]),
                "unsupported_cases": sum(1 for row in rows if row["unsupported"]),
            }
    finally:
        db.close()

    payload = {
        "run_at": datetime.now(UTC).isoformat(),
        "source": str(source_path),
        "models": source["models"],
        "cases": source["cases"],
        "by_case": by_case,
        "summary": summary,
    }
    md_path = write_report(payload)
    print(json.dumps({"status": "PASS", "summary": summary, "report": str(md_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
