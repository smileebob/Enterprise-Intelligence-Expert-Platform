"""初始化政务政策公开样本（导入 → 人工审核 → 发布），可重复执行。

前置：已执行 `alembic upgrade head`。只使用仓库 `政务智能体/samples/public/` 的公开样本。
用法（platform/ 目录）：
    ./.venv/Scripts/python.exe scripts/init_gov_demo.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
try:
    cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: S110 - 非交互控制台兜底
    pass

from app.db import SessionLocal  # noqa: E402
from app.gov_office.schemas import PolicyImportIn, PolicyReviewIn  # noqa: E402
from app.gov_office.services import GovPolicyService  # noqa: E402
from core.abstraction.dtos import Actor  # noqa: E402
from core.abstraction.enums import Roles  # noqa: E402
from core.admin.seed import seed_all  # noqa: E402

SAMPLE_DIR = REPO_ROOT / "政务智能体" / "samples" / "public"


async def _seed_samples(
    service: GovPolicyService, actor: Actor
) -> list[dict[str, Any]]:
    """同一事件循环内完成导入、审核、发布和嵌入，避免复用已关闭的模型客户端。"""
    results: list[dict[str, Any]] = []
    for path in sorted(SAMPLE_DIR.glob("*.json")):
        payload = PolicyImportIn.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        imported = service.import_policy(
            payload, actor, f"init-gov-{payload.document.doc_id}-{payload.version.version_label}"
        )
        version = service.get_version(
            payload.document.doc_id, payload.version.version_label
        )["version"]
        if version["review_status"] == "draft":
            service.review_version(
                payload.document.doc_id,
                payload.version.version_label,
                PolicyReviewIn(action="approve", note="公开样本开发初始化"),
                actor,
            )
        service.publish_document(payload.document.doc_id, actor)
        indexed = await service.build_embeddings(
            payload.document.doc_id, payload.version.version_label, actor
        )
        results.append(
            {
                "file": path.name,
                "doc_id": imported["doc_id"],
                "version_id": imported["version_id"],
                "duplicated": imported["duplicated"],
                "embedded": indexed["embedded"],
            }
        )
    return results


def main() -> int:
    actor = Actor(
        user_id="u-kbadmin",
        role_id=Roles.KB_ADMIN,
        permission_max=2,
        domain_scope=["chemical", "investment", "gov_office"],
    )
    db = SessionLocal()
    try:
        stats = seed_all(db)
        service = GovPolicyService(db)
        results = asyncio.run(_seed_samples(service, actor))
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "seed_domains": stats["domains"],
                    "samples": results,
                    "data_kind": "public_official_excerpt",
                    "review_kind": "engineering_fixture_not_human_review",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
