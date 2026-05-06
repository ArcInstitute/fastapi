"""Integration tests: real uvicorn server, real HTTP — both REST and MCP endpoints."""
import json
import socket
import subprocess
import sys
import time
from typing import Any

import httpx
import pytest

# ── unique tool IDs produced by FastAPI's generate_unique_id ─────────────────
TOOL_HELLO = "hello_hello__name__get"
TOOL_CREATE_ITEM = "create_item_items_post"  # /items has no braces → single underscore
TOOL_GET_ITEM = "get_item_items__item_id__get"

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
MCP_INIT_PARAMS = {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "integration-test", "version": "0.1"},
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server() -> str:  # type: ignore[return]
    """Spin up a real uvicorn process; yield base_url; tear it down."""
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.integration.hello_world_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.terminate()
        _, stderr = proc.communicate(timeout=3)
        pytest.fail(f"Server did not start.\nstderr: {stderr.decode()}")

    yield base_url

    proc.terminate()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()


# ── helpers ───────────────────────────────────────────────────────────────────


def _mcp(base_url: str, body: dict[str, Any]) -> httpx.Response:
    with httpx.Client(base_url=base_url, follow_redirects=True) as client:
        return client.post("/mcp/", json=body, headers=MCP_HEADERS)


def _list_tools(base_url: str) -> list[dict[str, Any]]:
    _mcp(
        base_url,
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": MCP_INIT_PARAMS},
    )
    resp = _mcp(
        base_url,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["tools"]


def _call_tool(base_url: str, name: str, arguments: dict[str, Any]) -> Any:
    resp = _mcp(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["content"][0]["text"]


# ── regular HTTP endpoint tests ───────────────────────────────────────────────


def test_root(live_server: str) -> None:
    resp = httpx.get(f"{live_server}/")
    assert resp.status_code == 200
    assert resp.json() == {"message": "Hello World"}


def test_hello_name(live_server: str) -> None:
    resp = httpx.get(f"{live_server}/hello/World")
    assert resp.status_code == 200
    assert resp.json() == {"message": "Hello, World!"}


def test_create_item(live_server: str) -> None:
    resp = httpx.post(
        f"{live_server}/items", json={"name": "Widget", "price": 9.99}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Widget"
    assert data["price"] == pytest.approx(9.99)


def test_get_item(live_server: str) -> None:
    resp = httpx.get(f"{live_server}/items/42")
    assert resp.status_code == 200
    assert resp.json() == {"item_id": 42}


# ── MCP endpoint tests ────────────────────────────────────────────────────────


def test_mcp_tools_listed(live_server: str) -> None:
    """All schema-visible routes must appear as MCP tools."""
    tool_names = [t["name"] for t in _list_tools(live_server)]
    assert TOOL_HELLO in tool_names, f"Missing {TOOL_HELLO!r}; got {tool_names}"
    assert TOOL_CREATE_ITEM in tool_names, f"Missing {TOOL_CREATE_ITEM!r}; got {tool_names}"
    assert TOOL_GET_ITEM in tool_names, f"Missing {TOOL_GET_ITEM!r}; got {tool_names}"


def test_mcp_call_hello(live_server: str) -> None:
    """Path-param route callable via MCP — returns greeting."""
    text = _call_tool(live_server, TOOL_HELLO, {"name": "MCP"})
    data = json.loads(text)
    assert data["message"] == "Hello, MCP!"


def test_mcp_call_create_item(live_server: str) -> None:
    """POST route with Pydantic body callable via MCP."""
    text = _call_tool(
        live_server, TOOL_CREATE_ITEM, {"body": {"name": "Widget", "price": 9.99}}
    )
    data = json.loads(text)
    assert data["name"] == "Widget"
    assert data["price"] == pytest.approx(9.99)


def test_mcp_call_get_item(live_server: str) -> None:
    """Numeric path param routed correctly via MCP."""
    text = _call_tool(live_server, TOOL_GET_ITEM, {"item_id": 42})
    data = json.loads(text)
    assert data["item_id"] == 42
