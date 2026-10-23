"""一键初始化演示环境（技术方案 06 §6.2）。

依次执行：建表（alembic upgrade head）→ 种子（4 角色 + 演示用户 + 双领域装配）
→ 导入 07 数据集（5 知识 JSON + 冲突基准 + 客户咨询）→ 向量化（入库管道内）
→ 冒烟校验（1 问答 + 1 冲突 + 1 评分对拍 83.8）→ PASS/FAIL 报告。

用法（platform/ 目录下）：
    ./.venv/Scripts/python.exe scripts/init_demo.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:  # Windows 控制台 GBK 兜底
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: S110 - 非交互环境下不支持 reconfigure 时静默降级
    pass

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402

KNOWLEDGE_FILES = ["products.json", "formulas.json", "processes.json", "experiment_cases.json", "external_knowledge.json"]


def _migrate() -> None:
    print("[1/6] alembic upgrade head ...")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"Alembic 迁移失败:\n{result.stderr[-2000:]}")


def _seed(db) -> None:
    print("[2/6] 种子数据（角色/用户/领域装配）...")
    from core.admin.seed import seed_all

    stats = seed_all(db)
    print(f"      users+{stats['users']} agents+{stats['agents']} domains+{stats['domains']} standards+{stats['standards']}")


def _ingest(db) -> None:
    print("[3/6] 导入 07 化学数据集（结构感知切分 + 六级打标 + 向量化）...")
    from core.knowledge.ingest import get_ingest_adapter
    from core.knowledge.service import KnowledgeService

    settings = get_settings()
    mock_dir = Path(settings.MOCK_DATA_DIR)
    service = KnowledgeService(db)
    for file_name in KNOWLEDGE_FILES:
        payload = json.loads((mock_dir / file_name).read_text(encoding="utf-8"))
        package = asyncio.run(service.ingest("chemical", file_name, payload))
        service.write_structured("chemical", payload, file_name)
        print(f"      {file_name}: docs={package.stats['docs']} chunks={package.stats['chunks']}")

    print("[4/6] 导入测试基准（冲突场景 + 客户咨询）...")
    adapter = get_ingest_adapter("chemical")
    conflicts_payload = json.loads((mock_dir / "conflict_scenarios.json").read_text(encoding="utf-8"))
    conflicts = adapter.build_conflicts_from_scenarios(conflicts_payload)
    service.repo.upsert_conflicts(conflicts)
    queries_payload = json.loads((mock_dir / "customer_queries.json").read_text(encoding="utf-8"))
    needs = adapter.seed_customer_needs(db, queries_payload)
    db.commit()
    print(f"      conflicts={len(conflicts)} customer_needs={needs}")


def _ingest_investment(db) -> None:
    print("      [副领域] 导入招商模拟数据 ...")
    from core.knowledge.service import KnowledgeService

    settings = get_settings()
    mock_dir = Path(settings.MOCK_DATA_DIR)
    payload = json.loads((mock_dir / "investment_mock.json").read_text(encoding="utf-8"))
    service = KnowledgeService(db)
    package = asyncio.run(service.ingest("investment", "investment_mock.json", payload))
    service.write_structured("investment", payload, "investment_mock.json")
    print(f"      investment_mock.json: docs={package.stats['docs']} chunks={package.stats['chunks']}")


def _smoke(db) -> dict:
    print("[5/6] 冒烟校验：1 问答 + 1 冲突 + 1 评分对拍 ...")
    from core.abstraction.dtos import Actor
    from core.abstraction.enums import Roles
    from core.evaluation.engine import EvaluationEngine
    from core.orchestration.engine import Orchestrator
    from core.orchestration.session import ChatSessionStore

    actor = Actor(user_id="u-expert", role_id=Roles.EXPERT, permission_max=2)
    store = ChatSessionStore(db)
    session = store.create_session("chemical", "u-expert", title="冒烟校验")
    orchestrator = Orchestrator(db)

    qa = asyncio.run(
        orchestrator.run_turn(session, "客户要涤纶面料做无氟防水，要求耐洗50次不明显下降，无氟能做到吗？", actor)
    )
    conflict_ok = any(c.conflict_id == "CS-01" for c in qa.state.conflicts)
    citations_ok = "[C" in (qa.text or "")

    engine = EvaluationEngine(db)
    golden = asyncio.run(
        engine.evaluate(
            {
                "domain_id": "chemical",
                "object_type": "solution",
                "object_ref": {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]},
                "scenario": "出口合规敏感",
            },
            actor=actor,
            persist=True,
        )
    )
    score_ok = abs(golden["total_score"] - 83.8) <= 0.1
    return {
        "qa_answered": bool(qa.text),
        "conflict_cs01": conflict_ok,
        "citations": citations_ok,
        "golden_83_8": {"score": golden["total_score"], "pass": score_ok},
    }


def main() -> int:
    settings = get_settings()
    print("=" * 72)
    print("企业级领域专家 Agent 平台 · 演示环境初始化")
    print(f"环境={settings.APP_ENV} 数据库={settings.DATABASE_URL} 模型模式={settings.DEMO_MODE}")
    print("=" * 72)
    _migrate()
    db = SessionLocal()
    try:
        _seed(db)
        _ingest(db)
        _ingest_investment(db)
        smoke = _smoke(db)
    finally:
        db.close()

    print("[6/6] 冒烟报告：")
    passed = smoke["qa_answered"] and smoke["conflict_cs01"] and smoke["citations"] and smoke["golden_83_8"]["pass"]
    for key, value in smoke.items():
        print(f"      {key}: {value}")
    print("=" * 72)
    print("[PASS] 初始化完成" if passed else "[FAIL] 冒烟校验未通过")
    print("提示：全部演示数据为虚构示例（is_mock）。")
    print("启动：uvicorn main:app --port 8000 （演示账号见 README）")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
