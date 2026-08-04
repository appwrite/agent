"""Agent long-term memory protocol — agent → Cloud persistence envelopes.

The `memory` tool does not talk to the database. It validates structured
set/forget actions and returns a canonical envelope. Cloud persists those
actions into `agentMemories` when it sees a successful `tool_end`.

Wire format version: appwrite.memory/v1
"""

from __future__ import annotations

import json
import re
from typing import Any

PROTOCOL = "appwrite.memory/v1"

ACTION_TYPES = frozenset({"set", "forget"})
CATEGORIES = frozenset({"preference", "instruction", "fact"})
SCOPES = frozenset({"user", "team", "project"})
# MVP: only user scope is writable. Team/project are reserved in the schema.
SUPPORTED_SCOPES = frozenset({"user"})

KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
MAX_ACTIONS = 10
MAX_CONTENT = 8192
MAX_PRIORITY = 1_000_000


class MemoryProtocolError(ValueError):
    """Invalid memory action payload."""


def _require_str(action: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = action.get(key)
    if not isinstance(value, str):
        raise MemoryProtocolError(f"{action.get('type')!r}: {key} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise MemoryProtocolError(f"{action.get('type')!r}: {key} is required")
    return text


def _optional_str(action: dict[str, Any], key: str) -> str | None:
    value = action.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemoryProtocolError(f"{action.get('type')!r}: {key} must be a string")
    text = value.strip()
    return text or None


def _optional_int(action: dict[str, Any], key: str) -> int | None:
    value = action.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MemoryProtocolError(f"{action.get('type')!r}: {key} must be an integer")
    return value


def validate_action(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MemoryProtocolError("each action must be an object")

    action_type = raw.get("type")
    if not isinstance(action_type, str) or action_type not in ACTION_TYPES:
        raise MemoryProtocolError(
            f"type must be one of {sorted(ACTION_TYPES)}; got {action_type!r}"
        )

    key = _require_str(raw, "key")
    if not KEY_RE.match(key):
        raise MemoryProtocolError(
            "key must match ^[a-z][a-z0-9_.-]{1,127}$ "
            f"(got {key!r})"
        )

    scope = _optional_str(raw, "scope") or "user"
    if scope not in SCOPES:
        raise MemoryProtocolError(f"scope must be one of {sorted(SCOPES)}")
    if scope not in SUPPORTED_SCOPES:
        raise MemoryProtocolError(
            f"scope={scope!r} is reserved; only scope=user is supported currently"
        )

    out: dict[str, Any] = {
        "type": action_type,
        "key": key,
        "scope": scope,
    }

    owner_id = _optional_str(raw, "ownerId")
    if owner_id is not None:
        out["ownerId"] = owner_id

    if action_type == "set":
        content = _require_str(raw, "content")
        if len(content) > MAX_CONTENT:
            raise MemoryProtocolError(
                f"content exceeds {MAX_CONTENT} characters"
            )
        category = _optional_str(raw, "category") or "preference"
        if category not in CATEGORIES:
            raise MemoryProtocolError(
                f"category must be one of {sorted(CATEGORIES)}"
            )
        priority = _optional_int(raw, "priority")
        if priority is None:
            priority = 0
        if priority < 0 or priority > MAX_PRIORITY:
            raise MemoryProtocolError(
                f"priority must be between 0 and {MAX_PRIORITY}"
            )
        out["content"] = content
        out["category"] = category
        out["priority"] = priority
    else:
        # forget — key + scope only
        for forbidden in ("content", "category", "priority"):
            if forbidden in raw and raw[forbidden] is not None:
                raise MemoryProtocolError(
                    f"forget actions must not include {forbidden}"
                )

    return out


def parse_actions_arg(actions: str | list | dict) -> list[dict[str, Any]]:
    """Accept a JSON string, a single action object, or an array of actions."""
    if isinstance(actions, dict):
        raw_list: list[Any] = [actions]
    elif isinstance(actions, list):
        raw_list = actions
    elif isinstance(actions, str):
        text = actions.strip()
        if not text:
            raise MemoryProtocolError("actions is empty")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MemoryProtocolError(f"actions must be valid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            raw_list = [parsed]
        elif isinstance(parsed, list):
            raw_list = parsed
        else:
            raise MemoryProtocolError(
                "actions JSON must be an object or an array of objects"
            )
    else:
        raise MemoryProtocolError("actions must be a JSON string, object, or array")

    if not raw_list:
        raise MemoryProtocolError("actions array is empty")
    if len(raw_list) > MAX_ACTIONS:
        raise MemoryProtocolError(f"at most {MAX_ACTIONS} memory actions per call")

    return [validate_action(item) for item in raw_list]


def build_envelope(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "actions": actions,
    }


def emit_memory_actions(actions: str | list | dict) -> str:
    """Validate actions and return the canonical JSON envelope string."""
    validated = parse_actions_arg(actions)
    envelope = build_envelope(validated)
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
