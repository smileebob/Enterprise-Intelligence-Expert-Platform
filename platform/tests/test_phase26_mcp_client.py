"""Phase 26 · MCP Client + SSRF 防护（白名单、DNS/IP、只读调用、结果有界）。"""

from __future__ import annotations

import asyncio
import os
import socket

import httpx2
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from app.mcp.client import (
    McpClientError,
    McpClientRejected,
    MCPClientService,
    McpServerConfig,
    validate_server_url,
)


def _resolver_for(ip: str):
    def resolve(host, port, type=None):  # noqa: A002
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return resolve


class TestSsrfValidation:
    def test_should_allow_public_https(self):
        validate_server_url("https://mcp.example.com/mcp", resolver=_resolver_for("93.184.216.34"))

    def test_should_reject_public_http(self):
        with pytest.raises(McpClientRejected):
            validate_server_url("http://mcp.example.com/mcp", resolver=_resolver_for("93.184.216.34"))

    def test_should_reject_metadata_link_local_even_with_private_allowed(self):
        with pytest.raises(McpClientRejected):
            validate_server_url(
                "http://mcp.internal/mcp",
                allow_private=True,
                resolver=_resolver_for("169.254.169.254"),
            )

    def test_should_reject_private_unless_explicitly_allowed(self):
        with pytest.raises(McpClientRejected):
            validate_server_url("https://mcp.internal/mcp", resolver=_resolver_for("10.0.0.8"))
        validate_server_url(
            "http://mcp.internal:8000/mcp",
            allow_private=True,
            resolver=_resolver_for("10.0.0.8"),
        )

    def test_should_reject_userinfo_and_nonstandard_public_port(self):
        with pytest.raises(McpClientRejected):
            validate_server_url(
                "https://user:pass@mcp.example.com/mcp", resolver=_resolver_for("93.184.216.34")
            )
        with pytest.raises(McpClientRejected):
            validate_server_url(
                "https://mcp.example.com:8443/mcp", resolver=_resolver_for("93.184.216.34")
            )


def _mock_server() -> MCPServer:
    server = MCPServer(name="mock-remote")

    @server.tool(name="echo", description="echo")
    async def echo(text: str) -> dict:
        return {"echo": text}

    @server.tool(name="big", description="big result")
    async def big() -> dict:
        return {"items": [{"index": index, "text": "x" * 100} for index in range(300)]}

    return server


class TestMcpClient:
    def _service(self, url: str, *, token_env: str = "") -> tuple[MCPClientService, object]:
        server = _mock_server()
        app = server.streamable_http_app(
            streamable_http_path="/",
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        config = McpServerConfig(
            server_id="mock",
            url=url,
            token_env=token_env,
            allow_private=True,
            timeout_s=5,
        )
        service = MCPClientService(
            servers={"mock": config},
            http_transport=httpx2.ASGITransport(app=app),
            resolver=_resolver_for("10.0.0.10"),
        )
        return service, app

    def test_should_list_and_call_remote_tools(self):
        service, app = self._service("http://mcp.internal/")

        async def run():
            async with app.router.lifespan_context(app):
                tools = await service.list_tools("mock")
                result = await service.call_tool("mock", "echo", {"text": "hi"})
                return tools, result

        tools, result = asyncio.run(run())
        assert {tool["name"] for tool in tools} == {"echo", "big"}
        assert result["ok"] is True
        assert result["source_type"] == "external"
        assert result["data"] == {"echo": "hi"}

    def test_should_bound_large_remote_result(self):
        service, app = self._service("http://mcp.internal/")

        async def run():
            async with app.router.lifespan_context(app):
                return await service.call_tool("mock", "big", {})

        result = asyncio.run(run())
        assert result["ok"] is True
        assert result["truncated"] is True
        assert len(result["data"]["items"]) <= 3

    def test_should_reject_unknown_server(self):
        service, _ = self._service("http://mcp.internal/")
        with pytest.raises(McpClientRejected):
            asyncio.run(service.list_tools("not-in-allowlist"))

    def test_should_fail_when_configured_token_is_missing(self):
        os.environ.pop("MCP_TEST_MISSING_TOKEN", None)
        service, _ = self._service("http://mcp.internal/", token_env="MCP_TEST_MISSING_TOKEN")
        with pytest.raises(McpClientError):
            asyncio.run(service.list_tools("mock"))
