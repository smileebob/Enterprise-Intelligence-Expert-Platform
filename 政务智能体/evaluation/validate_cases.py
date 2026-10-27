"""校验政务评测样本：Schema、重复 ID、拆分泄漏、引用 ID 可复算。

用法（仓库根目录）：
    python 政务智能体/evaluation/validate_cases.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import jsonschema

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CASES_DIR = HERE / "cases"
SAMPLE_DIR = ROOT / "政务智能体" / "samples" / "public"


def load_samples() -> dict[str, dict]:
    samples = {}
    for path in sorted(SAMPLE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        key = (
            payload["document"]["doc_id"],
            payload["version"]["version_label"],
        )
        samples[key] = payload
    return samples


def citation_id(doc_id: str, version_label: str, clause_no: str) -> str:
    digest = hashlib.sha256(f"{doc_id}:{version_label}|{clause_no}".encode()).hexdigest()
    return f"CL-{digest[:32]}@{version_label}"


def valid_citation_ids(samples: dict[str, dict]) -> set[str]:
    ids: set[str] = set()
    for (doc_id, label), payload in samples.items():
        for clause in payload.get("clauses", []):
            ids.add(citation_id(doc_id, label, clause["clause_no"]))
    return ids


def main() -> int:
    schema = json.loads((HERE / "cases.schema.json").read_text(encoding="utf-8"))
    samples = load_samples()
    known_versions = {label for _, label in samples}
    known_citations = valid_citation_ids(samples)

    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    group_split: dict[str, str] = {}
    counts: dict[str, int] = {}
    rows: list[dict] = []

    for path in sorted(CASES_DIR.glob("*.jsonl")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{line_no}"
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{where} JSON 解析失败：{exc}")
                continue
            rows.append(case)
            try:
                jsonschema.validate(case, schema)
            except jsonschema.ValidationError as exc:
                errors.append(f"{where} Schema 校验失败：{exc.message}")
                continue

            case_id = case["case_id"]
            if case_id in seen_ids:
                errors.append(f"{where} case_id 重复：{case_id}（另见 {seen_ids[case_id]}）")
            seen_ids[case_id] = where

            group = case["source_group"]
            split = case["split"]
            if group in group_split and group_split[group] != split:
                errors.append(
                    f"{where} 来源组 {group} 同时出现在 {group_split[group]} 与 {split}，存在泄漏"
                )
            group_split[group] = split
            counts[split] = counts.get(split, 0) + 1

            expected = case["expected"]
            for version in expected.get("required_policy_versions", []):
                if version not in known_versions:
                    errors.append(f"{where} 未知政策版本：{version}")
            for cid in expected.get("required_citation_ids", []):
                if cid not in known_citations:
                    errors.append(f"{where} 引用 ID 无法由公开样本复算：{cid}")

            as_of = case["input"].get("as_of")
            if as_of is not None:
                try:
                    from datetime import date

                    date.fromisoformat(as_of)
                except ValueError:
                    errors.append(f"{where} as_of 不是 ISO 日期：{as_of}")

    if errors:
        print("FAIL")
        for item in errors:
            print("-", item)
        return 1

    manifest_path = HERE / "manifest-v1.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("files", []):
            file_path = ROOT / entry["path"]
            if not file_path.exists():
                errors.append(f"冻结清单中的文件不存在：{entry['path']}")
                continue
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if digest != entry["sha256"]:
                errors.append(f"冻结清单哈希不匹配：{entry['path']}")
        expected_draft = manifest.get("label_status_counts", {}).get("draft")
        if expected_draft is not None:
            actual_draft = sum(1 for row in rows if row.get("label_status") == "draft")
            if actual_draft != expected_draft:
                errors.append(
                    f"冻结清单标注状态不一致：期望 draft {expected_draft}，实际 {actual_draft}"
                )
        if errors:
            print("FAIL")
            for item in errors:
                print("-", item)
            return 1

    print("PASS")
    print("cases:", len(rows), "splits:", counts, "groups:", sorted(group_split))
    print("public samples:", len(samples), "known citation ids:", len(known_citations))
    return 0


if __name__ == "__main__":
    sys.exit(main())
