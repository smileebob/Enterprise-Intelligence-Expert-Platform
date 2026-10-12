"""pytest 公共 fixture（规则 K-01/K-05）：SQLite 隔离库 + TestClient + 依赖替换 + 令牌缓存。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.models  # noqa: F401 - 注册全部 ORM 模型
from app import create_app
from app.db import Base, get_db

MOCK_DIR = Path("data/mock")
KNOWLEDGE_FILES = ["products.json", "formulas.json", "processes.json", "experiment_cases.json", "external_knowledge.json"]


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def db_session(db_factory, monkeypatch):
    """每个测试独立内存库；同时把 Celery 任务（eager）的 SessionLocal 指向同一工厂。"""
    session = db_factory()
    import app.db as app_db
    import core.tasks as core_tasks

    monkeypatch.setattr(app_db, "SessionLocal", db_factory, raising=False)
    monkeypatch.setattr(core_tasks, "SessionLocal", db_factory, raising=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def app(db_session):
    application = create_app("testing")
    application.dependency_overrides[get_db] = lambda: db_session
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


# ---------------- 数据种子 ----------------
def seed_users_and_permissions(db):
    from core.admin.seed import seed_all

    return seed_all(db)


def ingest_chemical_knowledge(db) -> None:
    import asyncio

    from core.knowledge.ingest import get_ingest_adapter
    from core.knowledge.service import KnowledgeService

    service = KnowledgeService(db)
    for file_name in KNOWLEDGE_FILES:
        payload = json.loads((MOCK_DIR / file_name).read_text(encoding="utf-8"))
        asyncio.run(service.ingest("chemical", file_name, payload))
        service.write_structured("chemical", payload, file_name)
    adapter = get_ingest_adapter("chemical")
    conflicts_payload = json.loads((MOCK_DIR / "conflict_scenarios.json").read_text(encoding="utf-8"))
    service.repo.upsert_conflicts(adapter.build_conflicts_from_scenarios(conflicts_payload))
    db.commit()


def ingest_investment_knowledge(db) -> None:
    import asyncio

    from core.knowledge.service import KnowledgeService

    payload = json.loads((MOCK_DIR / "investment_mock.json").read_text(encoding="utf-8"))
    service = KnowledgeService(db)
    asyncio.run(service.ingest("investment", "investment_mock.json", payload))
    service.write_structured("investment", payload, "investment_mock.json")
    db.commit()


@pytest.fixture()
def seeded(db_session):
    """用户+权限+双领域知识就绪（功能测试基础）。"""
    seed_users_and_permissions(db_session)
    ingest_chemical_knowledge(db_session)
    return db_session


_TOKEN_CACHE: dict[str, str] = {}


@pytest.fixture(scope="session")
def role_tokens():
    """模块级登录一次（避开登录限流 10/min）：JWT 与库无关，可在各测试复用。"""
    global _TOKEN_CACHE
    if _TOKEN_CACHE:
        return _TOKEN_CACHE
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    seed_users_and_permissions(session)
    application = create_app("testing")
    application.dependency_overrides[get_db] = lambda: session
    with TestClient(application) as test_client:
        credentials = {
            "expert": "Expert@12345",
            "kbadmin": "Kb@12345",
            "admin": "Admin@12345",
            "auditor": "Audit@12345",
        }
        for username, password in credentials.items():
            resp = test_client.post("/api/v1/auth/login", json={"username": username, "password": password})
            assert resp.status_code == 200, resp.text
            _TOKEN_CACHE[username] = resp.json()["data"]["access_token"]
    session.close()
    engine.dispose()
    return _TOKEN_CACHE


def auth(token_name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN_CACHE[token_name]}"}


@pytest.fixture()
def expert_headers(role_tokens):
    return auth("expert")


@pytest.fixture()
def kb_headers(role_tokens):
    return auth("kbadmin")


@pytest.fixture()
def admin_headers(role_tokens):
    return auth("admin")


@pytest.fixture()
def auditor_headers(role_tokens):
    return auth("auditor")
