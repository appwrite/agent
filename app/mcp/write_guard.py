"""Per-turn MCP create dedupe + 409 recovery.

State lives on the wrapped tool instance (closure), not ContextVars, so parallel
LangGraph tool calls in the same turn share it. MCP/Appwrite errors are often
raised as exceptions — those must be caught or recovery never runs.
"""

from __future__ import annotations

import asyncio
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


def _with_text(result: Any, text: str) -> Any:
    if isinstance(result, tuple):
        if len(result) == 1:
            return (text,)
        return (text, *result[1:])
    return text


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
                return (
                    f"Blocked duplicate {tool_name} in this turn.\n"
                    f"Previous result:\n{previous}\n\n"
                    "Use that result and finish. Do not create again."
                )

            if "arguments" in payload:
                payload["arguments"] = _expand_unique_in_obj(
                    payload["arguments"], ids=unique_ids
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
                    if result is not None:
                        return _with_text(result, recovered)
                    return recovered

                annotated = (
                    f"{text.rstrip()}\n\n"
                    "The resource already exists (likely created by this request). "
                    "Treat as success: list/get it and report it. Do NOT create again."
                )
                create_results[tool_name] = annotated
                if result is not None:
                    return _with_text(result, annotated)
                return annotated

            if error is not None:
                # Non-409 failures: surface as text so the model always gets tool_end.
                create_results[tool_name] = text if is_create else text
                return f"Error: {text}"

            if is_create:
                create_results[tool_name] = text
            return result if result is not None else text

    tool.coroutine = guarded_coroutine
    return tool
