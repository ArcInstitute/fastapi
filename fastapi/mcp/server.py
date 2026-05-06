from __future__ import annotations

import json
import logging
import re
import warnings
from typing import TYPE_CHECKING, Any

import anyio
import httpx
from mcp.server import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.types import CallToolResult, TextContent, Tool
from starlette.routing import BaseRoute
from starlette.types import Receive, Scope, Send

from fastapi.mcp.generator import get_mcp_tools
from fastapi.params import File, Form
from fastapi.routing import APIRoute

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MCPApp:
    """ASGI app that exposes FastAPI routes as MCP tools via Streamable HTTP."""

    def __init__(self, fastapi_app: Any) -> None:
        self._fastapi_app = fastapi_app
        self._server: Server[None, Any] = _build_server(fastapi_app)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            return

        transport = StreamableHTTPServerTransport(
            mcp_session_id=None,
            is_json_response_enabled=True,
            event_store=None,
        )

        server = self._server

        async def _run_server(
            *, task_status: Any = anyio.TASK_STATUS_IGNORED
        ) -> None:
            async with transport.connect() as (read_stream, write_stream):
                task_status.started()
                try:
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                        stateless=True,
                    )
                except Exception:
                    logger.exception("MCP server session error")

        async with anyio.create_task_group() as tg:
            await tg.start(_run_server)
            await transport.handle_request(scope, receive, send)
            await transport.terminate()


def _build_server(fastapi_app: Any) -> Server[None, Any]:
    server: Server[None, Any] = Server("FastAPI MCP")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        try:
            schema = fastapi_app.openapi()
        except Exception:
            return []
        return get_mcp_tools(fastapi_app.routes, schema)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        route = _find_route(fastapi_app.routes, name)
        if route is None:
            return [TextContent(type="text", text=f"Tool not found: {name}")]

        method = next(iter(route.methods or ["GET"]))
        path, query_params, body, is_form = _extract_call_parts(route, arguments)

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=fastapi_app),
                base_url="http://testserver",
            ) as client:
                resp = await client.request(
                    method=method,
                    url=path,
                    params=query_params or None,
                    data=body if is_form else None,
                    json=None if is_form else body,
                )
            return [TextContent(type="text", text=resp.text)]
        except Exception as exc:
            return [TextContent(type="text", text=f"Error calling {name}: {exc}")]

    return server


def _find_route(routes: list[BaseRoute], unique_id: str) -> APIRoute | None:
    for route in routes:
        if isinstance(route, APIRoute) and route.unique_id == unique_id:
            return route
    return None


def _extract_call_parts(
    route: APIRoute,
    arguments: dict[str, Any],
) -> tuple[str, dict[str, Any], Any, bool]:
    """Return (rendered_path, query_params, body_or_form_data, is_form) from tool arguments."""
    path = route.path
    query_params: dict[str, Any] = {}
    body: Any = None

    path_param_names: set[str] = {dep.name for dep in route.dependant.path_params}
    form_param_names: set[str] = {
        dep.name
        for dep in route.dependant.body_params
        if isinstance(dep.field_info, (Form, File))
    }
    is_form = bool(form_param_names)
    form_data: dict[str, Any] = {}

    for key, value in arguments.items():
        if key == "body":
            body = value
        elif key in path_param_names:
            path = re.sub(r"\{" + re.escape(key) + r"(?::[^}]+)?\}", str(value), path)
        elif key in form_param_names:
            form_data[key] = value
        else:
            query_params[key] = value

    return path, query_params, form_data if is_form else body, is_form
