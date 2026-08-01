"""Turn-scoped guards so MCP creates are not duplicated."""

from __future__ import annotations

import asyncio
import re
import secrets
import string
from contextvars import ContextVar
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

_ALPHABET = string.ascii_lowercase + string.digits

# Per-turn state (reset at the start of each /api/turn).
_lock: ContextVar[asyncio.Lock | None] = ContextVar("mcp_write_lock", default=None)
_unique_ids: ContextVar[dict[str, str] | None] = ContextVar(
    "mcp_unique_ids", default=None
)
_create_attempts: ContextVar[dict[str, int] | None] = ContextVar(
    "mcp_create_attempts", default=None
)
_create_results: ContextVar[dict[str, str] | None] = ContextVar(
    "mcp_create_results", default=None
)

_ALREADY_EXISTS_RE = re.compile(
    r"already_exists|already exists|code=409", re.IGNORECASE
)
_CREATE_TOOL_RE = re.compile(r"(?:^|_)create(?:_|$)")


def begin_turn_write_guard() -> None:
    """Call once at the start of each assistant turn."""
    _lock.set(asyncio.Lock())
    _unique_ids.set({})
    _create_attempts.set({})
    _create_results.set({})


def _state() -> tuple[asyncio.Lock, dict[str, str], dict[str, int], dict[str, str]]:
    lock = _lock.get()
    ids = _unique_ids.get()
    attempts = _create_attempts.get()
    results = _create_results.get()
    if lock is None or ids is None or attempts is None or results is None:
        begin_turn_write_guard()
        lock = _lock.get()
        ids = _unique_ids.get()
        attempts = _create_attempts.get()
        results = _create_results.get()
    assert lock is not None and ids is not None
    assert attempts is not None and results is not None
    return lock, ids, attempts, results


def _generate_id(length: int = 20) -> str:
    # Appwrite custom IDs: a-z A-Z 0-9 . - _; must not start with special char.
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


def _annotate_already_exists(text: str) -> str:
    return (
        f"{text.rstrip()}\n\n"
        "The resource already exists. Treat this as success for the user's "
        "request. Do NOT create again with unique() or another id — fetch/list "
        "the existing resource and report it."
    )


def _as_text(result: Any) -> str:
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


def wrap_mcp_tool(tool: BaseTool) -> BaseTool:
    """Attach error handling + create dedupe to an MCP-backed tool."""
    tool.handle_tool_error = True
    if tool.name != "appwrite_call_tool" or not isinstance(tool, StructuredTool):
        return tool

    original = tool.coroutine
    if original is None:
        return tool

    async def guarded_coroutine(**kwargs: Any) -> Any:
        lock, ids, attempts, results = _state()
        payload = dict(kwargs)
        tool_name = _tool_name_from_payload(payload)
        is_create = _is_create_tool(tool_name)

        async with lock:
            if is_create and attempts.get(tool_name, 0) >= 1:
                previous = results.get(tool_name, "")
                return (
                    f"Blocked duplicate {tool_name} in this turn to avoid creating "
                    f"multiple resources. Previous result:\n{previous}\n\n"
                    "If that result was already_exists, fetch/list the existing "
                    "resource and finish. Do not create again."
                )

            if "arguments" in payload:
                payload["arguments"] = _expand_unique_in_obj(
                    payload["arguments"], ids=ids
                )

            result = await original(**payload)
            text = _as_text(result)

            if is_create:
                attempts[tool_name] = attempts.get(tool_name, 0) + 1
                if _ALREADY_EXISTS_RE.search(text):
                    text = _annotate_already_exists(text)
                results[tool_name] = text
                return _with_text(result, text)

            if _ALREADY_EXISTS_RE.search(text):
                return _with_text(result, _annotate_already_exists(text))
            return result

    tool.coroutine = guarded_coroutine
    return tool
