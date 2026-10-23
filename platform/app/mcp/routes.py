"""MCP 受控客户端入口：短期 token 与信息查询（不暴露 OAuth 发现）。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.config import get_settings
from app.db import get_db
from app.deps import require_permissions
from app.mcp.tokens import create_mcp_token
from core.abstraction.dtos import Actor, AuditEntry
from core.runtime import audit_repo

router = APIRouter(tags=["mcp"])

READ_ONLY_TOOLS = [
    "search_products",
    "search_experiments",
    "search_external",
    "search_enterprises",
    "match_park_resources",
]


@router.post("/mcp/tokens", summary="签发 MCP 受控客户端短期 token")
def issue_mcp_token(
    actor: Actor = Depends(require_permissions("retrieval.search")),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    if not settings.MCP_ENABLED:
        raise biz("MCP_DISABLED", "MCP 接入未启用", 404)
    token, ttl, audience = create_mcp_token(actor)
    audit_repo(db).append(
        AuditEntry(
            trace_id="",
            actor_id=actor.user_id,
            action="mcp.token.issue",
            resource=audience,
            detail={"ttl": ttl, "scope": "mcp.read"},
        )
    )
    db.commit()
    return ok(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": ttl,
            "audience": audience,
            "scopes": ["mcp.read"],
            "tools": READ_ONLY_TOOLS,
        }
    )


class RemoteToolCallIn(BaseModel):
    tool: str = Field(min_length=1, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)


@router.get("/mcp/client/servers", summary="外部 MCP 白名单服务器")
def mcp_client_servers(
    actor: Actor = Depends(require_permissions("mcp.client.call")),
) -> dict:
    from app.mcp.client import MCPClientService

    return ok(MCPClientService().list_servers())


@router.get("/mcp/client/{server_id}/tools", summary="外部 MCP 工具发现（不落本地注册表）")
def mcp_client_tools(
    server_id: str,
    actor: Actor = Depends(require_permissions("mcp.client.call")),
) -> dict:
    from app.mcp.client import McpClientError, McpClientRejected, MCPClientService

    try:
        tools = asyncio.run(MCPClientService().list_tools(server_id))
    except McpClientRejected as exc:
        raise biz("MCP_CLIENT_REJECTED", str(exc), 400) from exc
    except McpClientError as exc:
        raise biz("MCP_CLIENT_UNAVAILABLE", str(exc), 409) from exc
    return ok(tools)


@router.post("/mcp/client/{server_id}/tools/call", summary="调用外部 MCP 只读工具（结果有界）")
def mcp_client_call(
    server_id: str,
    body: RemoteToolCallIn,
    actor: Actor = Depends(require_permissions("mcp.client.call")),
) -> dict:
    from app.mcp.client import McpClientError, McpClientRejected, MCPClientService

    try:
        result = asyncio.run(MCPClientService().call_tool(server_id, body.tool, body.args))
    except McpClientRejected as exc:
        raise biz("MCP_CLIENT_REJECTED", str(exc), 400) from exc
    except McpClientError as exc:
        raise biz("MCP_CLIENT_UNAVAILABLE", str(exc), 409) from exc
    return ok(result)


@router.get("/mcp/info", summary="MCP 受控接入信息")
def mcp_info(
    actor: Actor = Depends(require_permissions("retrieval.search")),
) -> dict:
    settings = get_settings()
    return ok(
        {
            "enabled": bool(settings.MCP_ENABLED),
            "mode": "controlled_client",
            "resource_url": settings.MCP_RESOURCE_URL,
            "tools": READ_ONLY_TOOLS,
            "note": "受控客户端接入；不是任意 MCP host 的 OAuth 兼容承诺。",
        }
    )
