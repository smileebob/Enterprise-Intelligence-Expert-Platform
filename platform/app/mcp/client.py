"""MCP Client（05/03 切片）：仅管理员白名单服务器、HTTPS/私网策略、只读调用、有界结果。"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx2

from app.config import get_settings
from core.clock import iso, utc_now
from core.orchestration.context import project_tool_data


class McpClientError(Exception):
    """外部 MCP 不可用/未授权/协议失败（不泄漏内部细节）。"""


class McpClientRejected(Exception):
    """地址或配置不安全（SSRF/明文/非白名单）。"""


@dataclass(frozen=True)
class McpServerConfig:
    server_id: str
    url: str
    token_env: str = ""
    allow_private: bool = False
    timeout_s: float = 10.0


def load_servers(raw: str | None = None) -> dict[str, McpServerConfig]:
    settings = get_settings()
    payload = raw if raw is not None else settings.MCP_CLIENT_SERVERS_JSON
    try:
        items = json.loads(payload or "[]")
    except ValueError as exc:
        raise McpClientRejected("MCP_CLIENT_SERVERS_JSON 不是合法 JSON") from exc
    servers: dict[str, McpServerConfig] = {}
    for item in items:
        if not isinstance(item, dict):
            raise McpClientRejected("MCP 服务器配置必须是对象")
        server_id = str(item.get("server_id") or "").strip()
        url = str(item.get("url") or "").strip()
        if not server_id or not url:
            raise McpClientRejected("MCP 服务器缺少 server_id/url")
        servers[server_id] = McpServerConfig(
            server_id=server_id,
            url=url,
            token_env=str(item.get("token_env") or ""),
            allow_private=bool(item.get("allow_private", False)),
            timeout_s=float(item.get("timeout_s") or settings.MCP_CLIENT_TIMEOUT_S),
        )
    return servers


def validate_server_url(
    url: str,
    *,
    allow_private: bool = False,
    resolver: Callable[..., list[Any]] = socket.getaddrinfo,
) -> None:
    """SSRF 基线：scheme/凭证/端口/解析地址检查；重定向由客户端禁用。"""
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http"):
        raise McpClientRejected("仅支持 http(s) MCP 地址")
    if parsed.scheme == "http" and not allow_private:
        raise McpClientRejected("外部 MCP 必须使用 HTTPS")
    if parsed.username or parsed.password:
        raise McpClientRejected("MCP 地址禁止携带凭证")
    host = parsed.hostname
    if not host:
        raise McpClientRejected("MCP 地址缺少主机")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not allow_private and port not in (80, 443):
        raise McpClientRejected("外部 MCP 仅允许标准端口")
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise McpClientRejected("MCP 主机无法解析") from exc
    if not infos:
        raise McpClientRejected("MCP 主机无法解析")
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise McpClientRejected("MCP 地址解析异常") from exc
        if (
            ip.is_unspecified
            or ip.is_multicast
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_loopback
            or ip.is_private
        ):
            if not (allow_private and (ip.is_loopback or ip.is_private)) or ip.is_link_local or ip.is_unspecified:
                raise McpClientRejected("MCP 地址指向受限网络")


class MCPClientService:
    def __init__(
        self,
        *,
        servers: dict[str, McpServerConfig] | None = None,
        http_transport: Any | None = None,
        resolver: Callable[..., list[Any]] = socket.getaddrinfo,
    ):
        self.servers = servers if servers is not None else load_servers()
        self.transport = http_transport
        self.resolver = resolver

    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {"server_id": cfg.server_id, "allow_private": cfg.allow_private}
            for cfg in self.servers.values()
        ]

    def _config(self, server_id: str) -> McpServerConfig:
        cfg = self.servers.get(server_id)
        if cfg is None:
            raise McpClientRejected("MCP 服务器不在白名单")
        validate_server_url(cfg.url, allow_private=cfg.allow_private, resolver=self.resolver)
        return cfg

    def _client(self, cfg: McpServerConfig) -> httpx2.AsyncClient:
        headers: dict[str, str] = {}
        if cfg.token_env:
            token = os.environ.get(cfg.token_env)
            if not token:
                raise McpClientError("外部 MCP 凭证不可用")
            headers["Authorization"] = f"Bearer {token}"
        return httpx2.AsyncClient(
            transport=self.transport,
            timeout=cfg.timeout_s,
            follow_redirects=False,
            headers=headers,
        )

    @staticmethod
    def _result_payload(result: Any, *, server_id: str, tool: str) -> dict[str, Any]:
        texts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                texts.append(str(text))
        raw = "\n".join(texts)
        parsed: Any = raw
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw
        view, projection = project_tool_data(
            parsed, inline_bytes=get_settings().TOOL_OUTPUT_INLINE_BYTES
        )
        return {
            "ok": not bool(getattr(result, "is_error", False)),
            "server_id": server_id,
            "tool": tool,
            "data": view,
            "truncated": projection["truncated"],
            "source_type": "external",
            "fetched_at": iso(utc_now()),
            "remote_is_error": bool(getattr(result, "is_error", False)),
        }

    async def list_tools(self, server_id: str) -> list[dict[str, Any]]:
        cfg = self._config(server_id)
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        try:
            async with self._client(cfg) as client:
                async with streamable_http_client(cfg.url, http_client=client) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        return [
                            {
                                "name": tool.name,
                                "description": tool.description or "",
                                "input_schema": getattr(tool, "inputSchema", None)
                                or getattr(tool, "input_schema", {}),
                            }
                            for tool in tools.tools
                        ]
        except McpClientError:
            raise
        except Exception as exc:
            raise McpClientError("外部 MCP 服务不可用") from exc

    async def call_tool(self, server_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        cfg = self._config(server_id)
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        try:
            async with self._client(cfg) as client:
                async with streamable_http_client(cfg.url, http_client=client) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool, args)
                        return self._result_payload(result, server_id=server_id, tool=tool)
        except McpClientError:
            raise
        except Exception as exc:
            raise McpClientError("外部 MCP 调用失败") from exc
