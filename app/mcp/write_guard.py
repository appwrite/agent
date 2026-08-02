"""Per-turn MCP create dedupe + 409 recovery.

State lives on the wrapped tool instance (closure), not ContextVars, so parallel
LangGraph tool calls in the same turn share it. MCP/Appwrite errors are often
raised as exceptions — those must be caught or recovery never runs.

MCP tools from langchain_mcp_adapters use response_format='content_and_artifact',
so every return from the wrapped coroutine must be a (content, artifact) 2-tuple.
Returning a plain string after catching ToolException raises ValueError inside
StructuredTool and drops tool_end from the stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import string
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)

_ALPHABET = string.ascii_lowercase + string.digits

_ALREADY_EXISTS_RE = re.compile(
    r"already_exists|already exists|code=409", re.IGNORECASE
)
_CREATE_TOOL_RE = re.compile(r"(?:^|_)create(?:_|$)")
_ID_KEYS = (
    "database_id",
    "bucket_id",
    "user_id",
    "team_id",
    "function_id",
    "file_id",
    "collection_id",
    "table_id",
    "row_id",
    "document_id",
)

# Models often pass SQL-ish filters; Appwrite expects Query JSON strings.
_QUERY_OP_RE = re.compile(
    r"^\s*(\$?[A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|!=|=|>|<)\s*(.+?)\s*$"
)
_QUERY_OP_MAP = {
    ">=": "greaterThanEqual",
    "<=": "lessThanEqual",
    ">": "greaterThan",
    "<": "lessThan",
    "=": "equal",
    "!=": "notEqual",
}
_QUERY_DOLLAR_ATTRS = frozenset({"createdAt", "updatedAt", "id", "sequence"})


def begin_turn_write_guard() -> None:
    """Kept for stream.py compatibility; state is per wrapped tool instance."""
    return


def _generate_id(length: int = 20) -> str:
    return secrets.choice(string.ascii_lowercase) + "".join(
        secrets.choice(_ALPHABET) for _ in range(length - 1)
    )


def _expand_unique(value: Any, *, slot: str, ids: dict[str, str]) -> Any:
    if value != "unique()":
        return value
    if slot not in ids:
        ids[slot] = _generate_id()
    return ids[slot]


def _expand_unique_in_obj(obj: Any, *, ids: dict[str, str], prefix: str = "") -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            slot = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(val, (dict, list)):
                out[key] = _expand_unique_in_obj(val, ids=ids, prefix=slot)
            else:
                out[key] = _expand_unique(val, slot=slot, ids=ids)
        return out
    if isinstance(obj, list):
        return [
            _expand_unique_in_obj(item, ids=ids, prefix=f"{prefix}[{i}]")
            for i, item in enumerate(obj)
        ]
    return _expand_unique(obj, slot=prefix or "value", ids=ids)


def _normalize_query_item(item: str) -> str:
    raw = item.strip()
    if not raw or raw.startswith("{") or raw.startswith("["):
        return raw

    match = _QUERY_OP_RE.match(raw)
    if not match:
        return raw

    attr, op, value = match.group(1), match.group(2), match.group(3).strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    if not attr.startswith("$") and attr in _QUERY_DOLLAR_ATTRS:
        attr = f"${attr}"
    method = _QUERY_OP_MAP.get(op)
    if not method:
        return raw
    return json.dumps(
        {"method": method, "attribute": attr, "values": [value]},
        separators=(",", ":"),
    )


def _normalize_arguments(arguments: Any) -> Any:
    if not isinstance(arguments, dict):
        return arguments
    queries = arguments.get("queries")
    if not isinstance(queries, list):
        return arguments
    out = dict(arguments)
    out["queries"] = [
        _normalize_query_item(item) if isinstance(item, str) else item
        for item in queries
    ]
    return out


def _tool_name_from_payload(payload: dict[str, Any]) -> str:
    name = payload.get("tool_name") or payload.get("toolName") or ""
    return str(name).strip()


def _is_create_tool(tool_name: str) -> bool:
    return bool(tool_name and _CREATE_TOOL_RE.search(tool_name))


def _get_tool_for_create(create_tool: str) -> str | None:
    if create_tool.endswith("_create"):
        return create_tool[: -len("_create")] + "_get"
    match = re.match(r"^(.+)_create_(.+)$", create_tool)
    if match:
        return f"{match.group(1)}_get_{match.group(2)}"
    return None


def _id_from_arguments(arguments: Any) -> tuple[str, str] | None:
    if not isinstance(arguments, dict):
        return None
    for key in _ID_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value and value != "unique()":
            return key, value
    for key, value in arguments.items():
        if (
            isinstance(key, str)
            and key.endswith("_id")
            and isinstance(value, str)
            and value
            and value != "unique()"
        ):
            return key, value
    return None


def _as_text(result: Any) -> str:
    if isinstance(result, BaseException):
        return str(result)
    if isinstance(result, tuple) and result:
        content = result[0]
        if isinstance(content, list):
            return "\n".join(str(part) for part in content)
        return content if isinstance(content, str) else str(content)
    if isinstance(result, list):
        return "\n".join(str(part) for part in result)
    return result if isinstance(result, str) else str(result)


def _content_and_artifact(text: str, result: Any = None) -> tuple[str, Any]:
    """MCP LangChain tools require response_format='content_and_artifact'."""
    if isinstance(result, tuple) and len(result) >= 2:
        return text, result[1]
    return text, None


def _is_already_exists(text: str) -> bool:
    return bool(_ALREADY_EXISTS_RE.search(text))


async def _recover_existing(
    original,
    *,
    payload: dict[str, Any],
    create_tool: str,
    arguments: Any,
) -> str | None:
    get_tool = _get_tool_for_create(create_tool)
    id_pair = _id_from_arguments(arguments)
    if not get_tool or not id_pair:
        return None

    id_key, id_value = id_pair
    get_payload: dict[str, Any] = {
        "tool_name": get_tool,
        "arguments": {id_key: id_value},
    }
    for key in ("project_id", "projectId", "organization_id", "organizationId"):
        if key in payload and payload[key] is not None:
            get_payload[key] = payload[key]

    try:
        fetched = await original(**get_payload)
    except Exception as exc:  # noqa: BLE001
        logger.info("409 recovery get failed for %s: %s", create_tool, exc)
        return None

    body = _as_text(fetched)
    if not body.strip():
        return None
    lowered = body.lower()
    if "request failed" in lowered and _is_already_exists(body):
        return None
    if lowered.startswith("error") and "ready" not in lowered:
        return None

    return (
        "Create completed successfully. Resource is ready "
        f"(id={id_value}).\n\n{body}\n\n"
        "Report this as a successful create to the user. Do not create again."
    )


def wrap_mcp_tool(tool: BaseTool) -> BaseTool:
    """Attach error handling + create dedupe to an MCP-backed tool."""
    tool.handle_tool_error = True
    if tool.name != "appwrite_call_tool" or not isinstance(tool, StructuredTool):
        return tool

    original = tool.coroutine
    if original is None:
        return tool

    # Per wrapped instance (== per turn, tools_from_connections rebuilds each turn).
    lock = asyncio.Lock()
    unique_ids: dict[str, str] = {}
    create_attempts: dict[str, int] = {}
    create_results: dict[str, str] = {}

    async def guarded_coroutine(**kwargs: Any) -> Any:
        payload = dict(kwargs)
        tool_name = _tool_name_from_payload(payload)
        is_create = _is_create_tool(tool_name)

        async with lock:
            if is_create and create_attempts.get(tool_name, 0) >= 1:
                previous = create_results.get(tool_name, "")
                return _content_and_artifact(
                    f"Blocked duplicate {tool_name} in this turn.\n"
                    f"Previous result:\n{previous}\n\n"
                    "Use that result and finish. Do not create again."
                )

            if "arguments" in payload:
                payload["arguments"] = _normalize_arguments(
                    _expand_unique_in_obj(payload["arguments"], ids=unique_ids)
                )

            if is_create:
                create_attempts[tool_name] = create_attempts.get(tool_name, 0) + 1

            try:
                result = await original(**payload)
                text = _as_text(result)
                error = None
            except Exception as exc:  # noqa: BLE001
                result = None
                text = str(exc)
                error = exc

            if is_create and _is_already_exists(text):
                recovered = await _recover_existing(
                    original,
                    payload=payload,
                    create_tool=tool_name,
                    arguments=payload.get("arguments"),
                )
                if recovered:
                    create_results[tool_name] = recovered
                    return _content_and_artifact(recovered, result)

                annotated = (
                    f"{text.rstrip()}\n\n"
                    "The resource already exists (likely created by this request). "
                    "Treat as success: list/get it and report it. Do NOT create again."
                )
                create_results[tool_name] = annotated
                return _content_and_artifact(annotated, result)

            if error is not None:
                # Non-409 failures: surface as text so the model always gets tool_end.
                if is_create:
                    create_results[tool_name] = text
                return _content_and_artifact(f"Error: {text}")

            if is_create:
                create_results[tool_name] = text
            if result is not None:
                return _content_and_artifact(text, result)
            return _content_and_artifact(text)

    tool.coroutine = guarded_coroutine
    return tool
