"""Phase 23 · MCP 受控客户端接入：token、Bearer/Origin、握手、只读白名单。"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager

import httpx2
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.models  # noqa: F401
from app.config import get_settings
from app.db import Base
from app.mcp.server import build_mcp_mount
from app.mcp.tokens import create_mcp_token, verify_mcp_token
from core.abstraction.dtos import Actor
from core.abstraction.enums import Roles
from tests.conftest import ingest_chemical_knowledge, seed_users_and_permissions

RESOURCE = "http://testserver/mcp"


@pytest.fixture()
def mcp_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "MCP_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MCP_RESOURCE_URL", RESOURCE, raising=False)
    return settings


@pytest.fixture()
def mcp_env(mcp_settings):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    seed_users_and_permissions(db)
    ingest_chemical_knowledge(db)
    db.close()

    mount = build_mcp_mount(factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mount.lifespan_app.router.lifespan_context(mount.lifespan_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", mount.asgi)
    token, _, _ = create_mcp_token(
        Actor(user_id="u-expert", role_id=Roles.EXPERT, permission_max=2, domain_scope=["chemical"])
    )
    yield app, token, factory
    engine.dispose()


def _expert_token() -> str:
    token, _, _ = create_mcp_token(
        Actor(user_id="u-expert", role_id=Roles.EXPERT, permission_max=2, domain_scope=["chemical"])
    )
    return token


class TestMcpToken:
    def test_should_issue_audience_bound_token_via_api(self, client, seeded, expert_headers, mcp_settings):
        resp = client.post("/api/v1/mcp/tokens", headers=expert_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["audience"] == RESOURCE
        assert data["token_type"] == "Bearer"
        assert "search_products" in data["tools"]
        assert verify_mcp_token(data["access_token"])["sub"] == "u-expert"

    def test_should_not_issue_token_when_mcp_disabled(self, client, seeded, expert_headers):
        resp = client.post("/api/v1/mcp/tokens", headers=expert_headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "MCP_DISABLED"

    def test_should_reject_wrong_audience_expired_and_wrong_type(self, mcp_settings):
        settings = get_settings()
        actor = Actor(user_id="u-expert", role_id=Roles.EXPERT, permission_max=2, domain_scope=["chemical"])
        token, _, _ = create_mcp_token(actor)
        assert verify_mcp_token(token) is not None

        wrong_aud = jwt.encode(
            {"sub": "u-expert", "aud": "http://evil/mcp", "exp": int(time.time()) + 60, "typ": "mcp_access", "scope": "mcp.read"},
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        assert verify_mcp_token(wrong_aud) is None

        expired = jwt.encode(
            {"sub": "u-expert", "aud": RESOURCE, "exp": int(time.time()) - 1, "typ": "mcp_access", "scope": "mcp.read"},
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        assert verify_mcp_token(expired) is None

        wrong_type = jwt.encode(
            {"sub": "u-expert", "aud": RESOURCE, "exp": int(time.time()) + 60, "typ": "user", "scope": "mcp.read"},
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        assert verify_mcp_token(wrong_type) is None


class TestMcpAuthGate:
    def test_should_reject_without_bearer(self, mcp_env):
        app, _, _ = mcp_env
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

        async def run():
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as http:
                    return await http.post("/mcp/", json=body)

        resp = asyncio.run(run())
        assert resp.status_code == 401
        assert resp.json()["error"] == "bearer_required"

    def test_should_reject_untrusted_origin(self, mcp_env):
        app, token, _ = mcp_env
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

        async def run():
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as http:
                    return await http.post(
                        "/mcp/",
                        json=body,
                        headers={"Authorization": f"Bearer {token}", "Origin": "http://evil.example"},
                    )

        resp = asyncio.run(run())
        assert resp.status_code == 403
        assert resp.json()["error"] == "origin_not_allowed"


class TestMcpHandshake:
    def test_should_initialize_list_readonly_tools_and_call(self, mcp_env):
        app, token, _ = mcp_env
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async def run():
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Authorization": f"Bearer {token}"},
                ) as http:
                    async with streamable_http_client("http://testserver/mcp/", http_client=http) as (read, write):
                        async with ClientSession(read, write) as session:
                            init = await session.initialize()
                            tools = await session.list_tools()
                            result = await session.call_tool("search_products", {"query": "防水"})
                            denied = await session.call_tool(
                                "search_enterprises", {"query": "AI制药"}
                            )
                            unknown = await session.call_tool("score_solution", {"object_ref": {}})
                            return init, tools, result, denied, unknown

        init, tools, result, denied, unknown = asyncio.run(run())
        assert init.server_info.name == "angentplatform"
        names = {tool.name for tool in tools.tools}
        assert names == {
            "search_products",
            "search_experiments",
            "search_external",
            "search_enterprises",
            "match_park_resources",
        }
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["data"]
        assert payload["source_type"] == "internal"
        # 化学专家不能调用招商工具；score_solution 未暴露
        assert denied.is_error is True
        assert unknown.is_error is True
