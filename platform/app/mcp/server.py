"""MCP Server（受控客户端接入）：只读工具经 ToolGateway 授权，Streamable HTTP。

安全边界：
- 外层 McpBearerMiddleware 验证专用 audience token；不做 OAuth 发现/任意 host 兼容承诺。
- tools/list 只暴露白名单只读工具；tools/call 仍逐次经 ToolGateway 做角色/域/权限/Schema 校验。
- 工具结果按上下文预算裁剪，不返回原始大对象。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy.orm import Session

from app.config import get_settings
from app.mcp.auth import McpBearerMiddleware
from core.abstraction.dtos import Actor
from core.abstraction.errors import ToolDeniedError
from core.gateway.permissions import permission_max_for_role
from core.gateway.service import ToolCtx, ToolGateway
from core.orchestration.context import project_tool_data

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass
class McpMount:
    asgi: Any
    lifespan_app: Any
    server: MCPServer


def _actor_from_context(ctx: Context, db: Session) -> Actor:
    request = ctx.request_context.request
    user = getattr(request, "user", None)
    access = getattr(user, "access_token", None)
    if access is None:
        raise ToolError("缺少受控客户端身份")
    subject = access.subject or access.client_id
    from core.admin.models import GovUser

    gov_user = db.get(GovUser, subject)
    if gov_user is None or gov_user.status != "active":
        raise ToolError("账号不可用")
    return Actor(
        user_id=gov_user.user_id,
        username=gov_user.username,
        role_id=gov_user.role_id,
        permission_max=permission_max_for_role(gov_user.role_id),
        domain_scope=list(gov_user.domain_scope or []),
    )


def _build_server(db_factory: Callable[[], Session]) -> MCPServer:
    server = MCPServer(
        name="angentplatform",
        title="企业级领域专家 Agent 平台（受控只读）",
        description="受控客户端接入；仅暴露只读检索工具，逐次经权限网关授权。",
        instructions=(
            "本 MCP Server 处于受控客户端阶段：只读检索工具，需要专用 audience token。"
            "不是任意 OAuth host 兼容实现；危险/写入工具不通过 MCP 暴露。"
        ),
        version="1.1.0",
    )

    async def invoke(ctx: Context, tool_name: str, domain_id: str, args: dict[str, Any]) -> dict[str, Any]:
        db = db_factory()
        try:
            actor = _actor_from_context(ctx, db)
            request = ctx.request_context.request
            if request is None:
                raise ToolError("缺少请求上下文")
            trace_id = request.headers.get("x-request-id") or f"MCP-{uuid.uuid4().hex[:12]}"
            gateway = ToolGateway(db)
            try:
                result = await gateway.invoke(
                    tool_name,
                    args,
                    ToolCtx(
                        db=db,
                        actor=actor,
                        session_id="mcp",
                        domain_id=domain_id,
                        trace_id=trace_id,
                        extra={"mcp": True},
                    ),
                )
            except ToolDeniedError as exc:
                raise ToolError(f"工具不可用或无权调用：{exc.code}") from exc
            if not result.ok:
                raise ToolError(result.message or "工具调用失败")
            view, projection = project_tool_data(
                result.data, inline_bytes=get_settings().TOOL_OUTPUT_INLINE_BYTES
            )
            return {
                "data": view,
                "source_type": (result.meta or {}).get("source_type", ""),
                "fetched_at": (result.meta or {}).get("fetched_at", ""),
                "truncated": projection["truncated"],
            }
        except ToolError:
            raise
        except Exception as exc:
            logger.exception("mcp tool %s failed", tool_name)
            raise ToolError("工具暂时不可用") from exc
        finally:
            db.close()

    @server.tool(
        name="search_products",
        title="检索内部产品",
        description="按面料/性能/类别关键词检索内部产品主数据（只读，授权范围内）。",
        annotations=READ_ONLY,
    )
    async def search_products(
        ctx: Context, query: str, fabric: str = "", category: str = ""
    ) -> dict[str, Any]:
        return await invoke(
            ctx, "search_products", "chemical", {"query": query, "fabric": fabric, "category": category}
        )

    @server.tool(
        name="search_experiments",
        title="检索历史实验",
        description="按产品/面料/性能指标检索内部历史实验报告（只读，授权范围内）。",
        annotations=READ_ONLY,
    )
    async def search_experiments(
        ctx: Context, product_id: str = "", fabric: str = "", metric: str = ""
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            "search_experiments",
            "chemical",
            {"product_id": product_id, "fabric": fabric, "metric": metric},
        )

    @server.tool(
        name="search_external",
        title="检索外部文献/标准",
        description="检索外部专利/文献/标准，返回强制来源标注（只读）。",
        annotations=READ_ONLY,
    )
    async def search_external(ctx: Context, query: str, source_type: str = "") -> dict[str, Any]:
        return await invoke(
            ctx, "search_external", "chemical", {"query": query, "type": source_type}
        )

    @server.tool(
        name="search_enterprises",
        title="检索招商企业",
        description="按产业方向/阶段/区域/关键词检索企业库（只读，招商领域授权）。",
        annotations=READ_ONLY,
    )
    async def search_enterprises(
        ctx: Context, track: str = "", stage: str = "", region: str = "", query: str = ""
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            "search_enterprises",
            "investment",
            {"track": track, "stage": stage, "region": region, "query": query},
        )

    @server.tool(
        name="match_park_resources",
        title="匹配园区资源",
        description="按企业需求匹配园区空间/政策/产业资源（只读，招商领域授权）。",
        annotations=READ_ONLY,
    )
    async def match_park_resources(
        ctx: Context, ent_id: str, needs: list[str] | None = None
    ) -> dict[str, Any]:
        return await invoke(
            ctx,
            "match_park_resources",
            "investment",
            {"ent_id": ent_id, "needs": needs or []},
        )

    return server


def build_mcp_mount(db_factory: Callable[[], Session] | None = None) -> McpMount:
    from app.db import SessionLocal
    from core.runtime import get_domain_registry

    get_domain_registry()  # 确保 @tool 注册表已挂载
    settings = get_settings()
    server = _build_server(db_factory or SessionLocal)
    lifespan_app = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.MCP_ALLOWED_HOSTS),
            allowed_origins=list(settings.MCP_ALLOWED_ORIGINS),
        ),
        host="127.0.0.1",
    )
    return McpMount(asgi=McpBearerMiddleware(lifespan_app), lifespan_app=lifespan_app, server=server)
