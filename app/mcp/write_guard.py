"""Per-turn MCP create dedupe + 409 recovery.

State lives on the wrapped tool instance (closure), not ContextVars, so parallel
LangGraph tool calls in the same turn share it. MCP/Appwrite errors are often
raised as exceptions — those must be caught or recovery never runs.

MCP tools from langchain_mcp_adapters use response_format='content_and_artifact',
so every return from the wrapped coroutine must be a (content, artifact) 2-tuple.
Returning a plain string after catching ToolException raises ValueError inside
StructuredTool and drops tool_end from the stream.

Create dedupe is keyed by concrete resource ids (not tool name alone), so
"create 5 users" with user_id=unique() can run five times. Shared unique()
slots still link foreign keys across tools (database_id on create then table).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import secrets
import string
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.attachment_upload import resolve_file_arguments

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

# Models often pass SQL-ish filters or SDK-style calls; Appwrite expects
# Query JSON *strings* (e.g. '{"method":"equal","attribute":"x","values":["y"]}').
_QUERY_OP_RE = re.compile(
    r"^\s*(\$?[A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|!=|=|>|<)\s*(.+?)\s*$"
)
_QUERY_CALL_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$",
    re.DOTALL,
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
# SDK helpers that take only value(s) — no attribute as first arg.
_QUERY_VALUE_ONLY = frozenset(
    {
        "limit",
        "offset",
        "cursorAfter",
        "cursorBefore",
        "select",
        "orderRandom",
        "createdBefore",
        "createdAfter",
        "createdBetween",
        "updatedBefore",
        "updatedAfter",
        "updatedBetween",
        "or",
        "and",
        "exists",
        "notExists",
    }
)
_QUERY_ATTR_ONLY = frozenset(
    {
        "isNull",
        "isNotNull",
        "orderAsc",
        "orderDesc",
    }
)
_QUERY_TIME_HELPERS = {
    "createdBefore": ("lessThan", "$createdAt"),
    "createdAfter": ("greaterThan", "$createdAt"),
    "updatedBefore": ("lessThan", "$updatedAt"),
    "updatedAfter": ("greaterThan", "$updatedAt"),
}


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


def _expand_unique_in_obj(
    obj: Any,
    *,
    ids: dict[str, str],
    prefix: str = "",
    fresh_keys: frozenset[str] | None = None,
    attempt: int = 0,
) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            slot = f"{prefix}.{key}" if prefix else str(key)
            # Same create tool called again: mint a new id for its own id keys
            # so batch unique() creates do not collide. Foreign keys keep the
            # shared slot (database_id on table create after database create).
            if (
                fresh_keys
                and attempt > 0
                and isinstance(key, str)
                and key in fresh_keys
                and val == "unique()"
            ):
                slot = f"{slot}#{attempt}"
            if isinstance(val, (dict, list)):
                out[key] = _expand_unique_in_obj(
                    val,
                    ids=ids,
                    prefix=slot,
                    fresh_keys=fresh_keys,
                    attempt=attempt,
                )
            else:
                out[key] = _expand_unique(val, slot=slot, ids=ids)
        return out
    if isinstance(obj, list):
        return [
            _expand_unique_in_obj(
                item,
                ids=ids,
                prefix=f"{prefix}[{i}]",
                fresh_keys=fresh_keys,
                attempt=attempt,
            )
            for i, item in enumerate(obj)
        ]
    return _expand_unique(obj, slot=prefix or "value", ids=ids)


def _canonical_attr(attr: str) -> str:
    if not attr.startswith("$") and attr in _QUERY_DOLLAR_ATTRS:
        return f"${attr}"
    return attr


def _dump_query(
    *,
    method: str,
    attribute: str | None = None,
    values: list[Any] | None = None,
) -> str:
    payload: dict[str, Any] = {"method": method}
    if attribute is not None:
        payload["attribute"] = _canonical_attr(attribute)
    if values is not None:
        payload["values"] = values
    return json.dumps(payload, separators=(",", ":"))


def _normalize_query_dict(item: dict[str, Any]) -> str:
    method = item.get("method")
    if not isinstance(method, str) or not method.strip():
        return json.dumps(item, separators=(",", ":"))

    attribute = item.get("attribute")
    if isinstance(attribute, str):
        attribute = _canonical_attr(attribute)
    elif attribute is not None:
        attribute = str(attribute)

    values = item.get("values")
    if values is not None and not isinstance(values, list):
        values = [values]

    return _dump_query(
        method=method.strip(),
        attribute=attribute if isinstance(attribute, str) else None,
        values=values if isinstance(values, list) else None,
    )


def _parse_query_call_args(args_str: str) -> tuple[Any, ...] | None:
    stripped = args_str.strip()
    if not stripped:
        return ()
    try:
        parsed = ast.literal_eval(f"({stripped},)")
    except (ValueError, SyntaxError):
        return None
    if not isinstance(parsed, tuple):
        return (parsed,)
    return parsed


def _normalize_query_call(raw: str) -> str | None:
    match = _QUERY_CALL_RE.match(raw)
    if not match:
        return None
    method, args_str = match.group(1), match.group(2)
    args = _parse_query_call_args(args_str)
    if args is None:
        return None

    time_helper = _QUERY_TIME_HELPERS.get(method)
    if time_helper and len(args) == 1:
        mapped_method, attribute = time_helper
        return _dump_query(
            method=mapped_method,
            attribute=attribute,
            values=[args[0]],
        )
    if method in {"createdBetween", "updatedBetween"} and len(args) == 2:
        attribute = "$createdAt" if method == "createdBetween" else "$updatedAt"
        return _dump_query(
            method="between",
            attribute=attribute,
            values=[args[0], args[1]],
        )
    if method in _QUERY_VALUE_ONLY:
        if not args:
            return _dump_query(method=method)
        if len(args) == 1 and isinstance(args[0], list):
            return _dump_query(method=method, values=args[0])
        return _dump_query(method=method, values=list(args))
    if method in _QUERY_ATTR_ONLY and len(args) == 1 and isinstance(args[0], str):
        return _dump_query(method=method, attribute=args[0])
    if not args or not isinstance(args[0], str):
        return None

    attribute = args[0]
    rest = list(args[1:])
    if len(rest) == 1 and isinstance(rest[0], list):
        values = rest[0]
    else:
        values = rest
    return _dump_query(
        method=method,
        attribute=attribute,
        values=values if values else None,
    )


def _normalize_query_item(item: Any) -> Any:
    if isinstance(item, dict):
        return _normalize_query_dict(item)
    if not isinstance(item, str):
        return item

    raw = item.strip()
    if not raw:
        return raw
    if raw.startswith("{") or raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(parsed, dict):
            return _normalize_query_dict(parsed)
        return raw

    match = _QUERY_OP_RE.match(raw)
    if match:
        attr, op, value = match.group(1), match.group(2), match.group(3).strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        method = _QUERY_OP_MAP.get(op)
        if method:
            return _dump_query(method=method, attribute=attr, values=[value])

    converted = _normalize_query_call(raw)
    if converted is not None:
        return converted
    return raw


def _normalize_arguments(arguments: Any) -> Any:
    if not isinstance(arguments, dict):
        return arguments
    out = resolve_file_arguments(arguments)
    queries = out.get("queries")
    if not isinstance(queries, list):
        return out
    normalized = dict(out)
    normalized["queries"] = [_normalize_query_item(item) for item in queries]
    return normalized


def _tool_name_from_payload(payload: dict[str, Any]) -> str:
    name = payload.get("tool_name") or payload.get("toolName") or ""
    return str(name).strip()


def _is_create_tool(tool_name: str) -> bool:
    return bool(tool_name and _CREATE_TOOL_RE.search(tool_name))


def _own_id_keys_for_create(create_tool: str) -> frozenset[str]:
    """Id fields owned by this create (users_create → user_id)."""
    match = re.match(r"^([a-z0-9]+)_create", create_tool)
    if not match:
        return frozenset()
    resource = match.group(1)
    if resource.endswith("ies"):
        singular = resource[:-3] + "y"
    elif resource.endswith("s") and not resource.endswith("ss"):
        singular = resource[:-1]
    else:
        singular = resource
    return frozenset({f"{singular}_id"})


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


def _dedupe_key(tool_name: str, arguments: Any) -> str | None:
    """Stable key for concrete-id retries; None means ephemeral (unique()) create."""
    id_pair = _id_from_arguments(arguments)
    if not id_pair:
        return None
    id_key, id_value = id_pair
    return f"{tool_name}:{id_key}={id_value}"


def _as_text(result: Any) -> str:
    from app.graph.content import content_to_text

    if isinstance(result, BaseException):
        return str(result)
    if isinstance(result, tuple) and result:
        return content_to_text(result[0])
    return content_to_text(result)


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
        raw_arguments = payload.get("arguments")

        async with lock:
            dedupe = _dedupe_key(tool_name, raw_arguments) if is_create else None
            if is_create and dedupe and dedupe in create_results:
                previous = create_results[dedupe]
                return _content_and_artifact(
                    f"Blocked duplicate {tool_name} for the same id in this turn.\n"
                    f"Previous result:\n{previous}\n\n"
                    "This create did NOT run again. Use the previous result. "
                    "Do not claim a new resource was created."
                )

            attempt = create_attempts.get(tool_name, 0) if is_create else 0
            own_keys = _own_id_keys_for_create(tool_name) if is_create else frozenset()

            if "arguments" in payload:
                payload["arguments"] = _normalize_arguments(
                    _expand_unique_in_obj(
                        payload["arguments"],
                        ids=unique_ids,
                        fresh_keys=own_keys,
                        attempt=attempt,
                    )
                )

            # After unique() expansion, concrete ids may collide across batch
            # calls that somehow shared a slot — re-check expanded args.
            if is_create:
                expanded_dedupe = _dedupe_key(tool_name, payload.get("arguments"))
                if expanded_dedupe and expanded_dedupe in create_results:
                    previous = create_results[expanded_dedupe]
                    return _content_and_artifact(
                        f"Blocked duplicate {tool_name} for the same id in this turn.\n"
                        f"Previous result:\n{previous}\n\n"
                        "This create did NOT run again. Use the previous result. "
                        "Do not claim a new resource was created."
                    )
                dedupe = expanded_dedupe
                create_attempts[tool_name] = attempt + 1

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
                    if dedupe:
                        create_results[dedupe] = recovered
                    return _content_and_artifact(recovered, result)

                annotated = (
                    f"{text.rstrip()}\n\n"
                    "The resource already exists (likely created by this request). "
                    "Treat as success: list/get it and report it. Do NOT create again."
                )
                if dedupe:
                    create_results[dedupe] = annotated
                return _content_and_artifact(annotated, result)

            if error is not None:
                # Non-409 failures: surface as text so the model always gets tool_end.
                if is_create and dedupe:
                    create_results[dedupe] = text
                return _content_and_artifact(f"Error: {text}")

            if is_create and dedupe:
                create_results[dedupe] = text
            if result is not None:
                return _content_and_artifact(text, result)
            return _content_and_artifact(text)

    tool.coroutine = guarded_coroutine
    return tool
