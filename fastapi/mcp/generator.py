import re
from typing import Any

from mcp.types import Tool
from starlette.routing import BaseRoute

from fastapi.routing import APIRoute


def _resolve_ref(ref: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve a $ref within the OpenAPI schema."""
    parts = ref.lstrip("#/").split("/")
    node: Any = schema
    for part in parts:
        node = node[part]
    return dict(node)


def _build_input_schema(
    operation: dict[str, Any],
    full_schema: dict[str, Any],
) -> dict[str, Any]:
    """Build a JSON Schema input object for an MCP tool from an OpenAPI operation."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in operation.get("parameters", []):
        if param.get("in") not in ("path", "query"):
            continue
        name = param["name"]
        param_schema = param.get("schema", {"type": "string"})
        if "$ref" in param_schema:
            param_schema = _resolve_ref(param_schema["$ref"], full_schema)
        prop: dict[str, Any] = dict(param_schema)
        if param.get("description"):
            prop["description"] = param["description"]
        properties[name] = prop
        if param.get("required") or param.get("in") == "path":
            required.append(name)

    request_body = operation.get("requestBody")
    if request_body:
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        if json_content:
            body_schema = json_content.get("schema", {})
            if "$ref" in body_schema:
                body_schema = _resolve_ref(body_schema["$ref"], full_schema)
            properties["body"] = body_schema
            if request_body.get("required"):
                required.append("body")
        else:
            form_content = content.get("multipart/form-data") or content.get(
                "application/x-www-form-urlencoded"
            )
            if form_content:
                form_schema = form_content.get("schema", {})
                if "$ref" in form_schema:
                    form_schema = _resolve_ref(form_schema["$ref"], full_schema)
                for field_name, field_schema in form_schema.get("properties", {}).items():
                    properties[field_name] = dict(field_schema)
                required.extend(form_schema.get("required", []))

    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def get_mcp_tools(
    routes: list[BaseRoute],
    openapi_schema: dict[str, Any],
) -> list[Tool]:
    """Convert FastAPI APIRoute objects to MCP Tool definitions."""
    tools: list[Tool] = []
    paths = openapi_schema.get("paths", {})

    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.include_in_schema:
            continue

        # OpenAPI strips converter syntax ({name:type} → {name}), so normalize
        openapi_path = re.sub(r"\{(\w+):[^}]+\}", r"{\1}", route.path)
        path_item = paths.get(openapi_path, {})
        for method in route.methods or []:
            method_lower = method.lower()
            operation = path_item.get(method_lower)
            if operation is None:
                continue

            description = (
                operation.get("summary")
                or operation.get("description")
                or f"{method} {route.path}"
            )
            input_schema = _build_input_schema(operation, openapi_schema)

            tools.append(
                Tool(
                    name=route.unique_id,
                    description=description,
                    inputSchema=input_schema,
                )
            )

    return tools
