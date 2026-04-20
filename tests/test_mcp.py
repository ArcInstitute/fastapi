"""Tests for FastAPI native MCP support (fastapi/mcp/)."""
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

MCP_INIT_PARAMS = {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "test-client", "version": "0.1"},
}


def make_client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=True,
    )


async def _mcp_post(
    client: httpx.AsyncClient, url: str, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        url,
        json=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )


async def _list_tools(
    client: httpx.AsyncClient, mcp_url: str
) -> list[dict[str, Any]]:
    """Initialize MCP session and return tool list."""
    init_resp = await _mcp_post(
        client,
        mcp_url,
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": MCP_INIT_PARAMS},
    )
    assert init_resp.status_code == 200, f"Init failed: {init_resp.text}"
    assert "result" in init_resp.json()

    tools_resp = await _mcp_post(
        client,
        mcp_url,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert tools_resp.status_code == 200, f"tools/list failed: {tools_resp.text}"
    return tools_resp.json().get("result", {}).get("tools", [])


@pytest.mark.anyio
async def test_mcp_disabled_by_default() -> None:
    app = FastAPI()

    @app.get("/items")
    async def list_items() -> list[str]:
        return ["a"]

    async with make_client(app) as client:
        resp = await client.post("/mcp")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_mcp_custom_url() -> None:
    app = FastAPI(mcp_url="/api/mcp")

    @app.get("/items")
    async def list_items() -> list[str]:
        return ["a"]

    async with make_client(app) as client:
        resp = await _mcp_post(
            client,
            "/api/mcp",
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": MCP_INIT_PARAMS,
            },
        )
    assert resp.status_code == 200
    assert "result" in resp.json()


@pytest.mark.anyio
async def test_mcp_tools_list() -> None:
    app = FastAPI(mcp_url="/mcp")

    @app.get("/items", summary="List items")
    async def list_items() -> list[str]:
        return ["a"]

    @app.post("/items", summary="Create item")
    async def create_item(name: str) -> dict[str, str]:
        return {"name": name}

    async with make_client(app) as client:
        tools = await _list_tools(client, "/mcp")

    tool_names = [t["name"] for t in tools]
    assert any("list_items" in n for n in tool_names)
    assert any("create_item" in n for n in tool_names)


@pytest.mark.anyio
async def test_include_in_schema_false_excluded() -> None:
    app = FastAPI(mcp_url="/mcp")

    @app.get("/public")
    async def public_route() -> str:
        return "ok"

    @app.get("/private", include_in_schema=False)
    async def private_route() -> str:
        return "secret"

    async with make_client(app) as client:
        tools = await _list_tools(client, "/mcp")

    tool_names = [t["name"] for t in tools]
    assert any("public" in n for n in tool_names)
    assert not any("private" in n for n in tool_names)


@pytest.mark.anyio
async def test_mcp_tool_call_get() -> None:
    app = FastAPI(mcp_url="/mcp")

    @app.get("/hello", summary="Say hello")
    async def hello() -> dict[str, str]:
        return {"message": "hello"}

    async with make_client(app) as client:
        tools = await _list_tools(client, "/mcp")
        hello_tool = next((t for t in tools if "hello" in t["name"]), None)
        assert hello_tool is not None

        call_resp = await _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": hello_tool["name"], "arguments": {}},
            },
        )
    assert call_resp.status_code == 200
    result = call_resp.json()
    assert "result" in result
    content = result["result"]["content"]
    assert len(content) > 0
    text = content[0]["text"]
    assert "hello" in text


@pytest.mark.anyio
async def test_mcp_tool_call_post_with_body() -> None:
    from pydantic import BaseModel

    app = FastAPI(mcp_url="/mcp")

    class Item(BaseModel):
        name: str
        price: float

    @app.post("/items", summary="Create item")
    async def create_item(item: Item) -> Item:
        return item

    async with make_client(app) as client:
        tools = await _list_tools(client, "/mcp")
        create_tool = next((t for t in tools if "create_item" in t["name"]), None)
        assert create_tool is not None

        call_resp = await _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": create_tool["name"],
                    "arguments": {"body": {"name": "Widget", "price": 9.99}},
                },
            },
        )
    assert call_resp.status_code == 200
    result = call_resp.json()
    assert "result" in result
    text = result["result"]["content"][0]["text"]
    data = json.loads(text)
    assert data["name"] == "Widget"
    assert data["price"] == pytest.approx(9.99)


@pytest.mark.anyio
async def test_mcp_path_params() -> None:
    app = FastAPI(mcp_url="/mcp")

    @app.get("/items/{item_id}", summary="Get item")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    async with make_client(app) as client:
        tools = await _list_tools(client, "/mcp")
        get_tool = next((t for t in tools if "get_item" in t["name"]), None)
        assert get_tool is not None

        call_resp = await _mcp_post(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": get_tool["name"],
                    "arguments": {"item_id": 42},
                },
            },
        )
    assert call_resp.status_code == 200
    result = call_resp.json()
    text = result["result"]["content"][0]["text"]
    data = json.loads(text)
    assert data["item_id"] == 42
