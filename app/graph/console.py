"""Appwrite Console UI protocol — agent → console metadata envelopes.

The `console` tool does not mutate Appwrite resources. It posts structured
actions that the Console (vibes) turns into UI: theme changes, navigation,
toasts, create dialogs, resource cards, and resource lists (instead of
markdown tables).

Wire format version: appwrite.console/v1
Full contract: docs/console-protocol.md
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL = "appwrite.console/v1"
PROTOCOL_DOC = "docs/console-protocol.md"

THEMES = frozenset({"light", "dark", "system"})
MUTATIONS = frozenset({"create", "update", "delete"})
CREATE_RESOURCES = frozenset(
    {"database", "bucket", "user", "team", "function", "site"}
)
DIALOGS = frozenset(
    {
        "invite_member",
        "create_project",
        "connect_mcp",
        "shortcuts",
        "docs_search",
        "feedback",
        "support",
    }
)
PANE_CONTENTS = frozenset({"agent", "docs", "none"})
TOAST_LEVELS = frozenset({"success", "error", "info", "warning"})
ACTION_TYPES = frozenset(
    {
        "set_theme",
        "navigate",
        "open_create",
        "open_dialog",
        "toast",
        "show_pane",
        "toggle_terminal",
        "scroll_to_card",
        "resource",
        "resource_list",
        "chart",
        "refresh",
    }
)

CHART_TYPES = frozenset({"area", "bar"})
CHART_KINDS = frozenset({"events", "gauges"})
CHART_AXIS_FORMATS = frozenset({"count", "bytes", "gbhours"})

MAX_ACTIONS = 20
MAX_LIST_ITEMS = 50
MAX_METADATA = 20
MAX_COLUMNS = 12
MAX_CHART_METRICS = 10
MAX_CHART_POINTS = 5000
FIELD_VALUE_TYPES = (str, int, float, bool, type(None))


class ConsoleProtocolError(ValueError):
    """Invalid console action payload."""


def _require_str(action: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = action.get(key)
    if not isinstance(value, str):
        raise ConsoleProtocolError(f"{action.get('type')!r}: {key} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise ConsoleProtocolError(f"{action.get('type')!r}: {key} is required")
    return text


def _optional_str(action: dict[str, Any], key: str) -> str | None:
    value = action.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConsoleProtocolError(f"{action.get('type')!r}: {key} must be a string")
    text = value.strip()
    return text or None


def _optional_bool(action: dict[str, Any], key: str) -> bool | None:
    value = action.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConsoleProtocolError(f"{action.get('type')!r}: {key} must be a boolean")
    return value


def _optional_non_neg_int(action: dict[str, Any], key: str) -> int | None:
    value = action.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConsoleProtocolError(f"{action.get('type')!r}: {key} must be an integer")
    if value < 0:
        raise ConsoleProtocolError(f"{action.get('type')!r}: {key} must be >= 0")
    return value


def _validate_metadata(
    raw: Any, *, context: str
) -> list[dict[str, str]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ConsoleProtocolError(f"{context}: metadata must be an array")
    if len(raw) > MAX_METADATA:
        raise ConsoleProtocolError(
            f"{context}: at most {MAX_METADATA} metadata entries"
        )
    items: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConsoleProtocolError(f"{context}: metadata items must be objects")
        label = item.get("label")
        value = item.get("value")
        if not isinstance(label, str) or not label.strip():
            raise ConsoleProtocolError(
                f"{context}: metadata[].label must be a non-empty string"
            )
        if value is None:
            raise ConsoleProtocolError(f"{context}: metadata[].value is required")
        items.append(
            {
                "label": label.strip(),
                "value": value if isinstance(value, str) else str(value),
            }
        )
    return items


def _validate_fields(
    raw: Any, *, context: str
) -> dict[str, str | int | float | bool | None] | None:
    """Optional typed map for Console-side filters (email, status, enabled, …)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConsoleProtocolError(f"{context}: fields must be an object")
    if len(raw) > 40:
        raise ConsoleProtocolError(f"{context}: at most 40 field entries")
    out: dict[str, str | int | float | bool | None] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ConsoleProtocolError(
                f"{context}: fields keys must be non-empty strings"
            )
        if not isinstance(value, FIELD_VALUE_TYPES):
            raise ConsoleProtocolError(
                f"{context}: fields.{key} must be string, number, boolean, or null"
            )
        if isinstance(value, str):
            out[key.strip()] = value
        else:
            out[key.strip()] = value
    return out


def _validate_resource_item(raw: Any, *, context: str) -> dict[str, Any]:
    """Shared shape for single resource cards and list rows."""
    if not isinstance(raw, dict):
        raise ConsoleProtocolError(f"{context}: item must be an object")
    # Allow callers to pass resourceType on items; list action owns the type.
    out: dict[str, Any] = {
        "resourceId": _require_str(raw, "resourceId"),
        "title": _require_str(raw, "title"),
    }
    for key in ("subtitle", "href", "status"):
        value = _optional_str(raw, key)
        if value is not None:
            out[key] = value
    metadata = _validate_metadata(raw.get("metadata"), context=context)
    if metadata is not None:
        out["metadata"] = metadata
    fields = _validate_fields(raw.get("fields"), context=context)
    if fields is not None:
        out["fields"] = fields
    return out


def _validate_columns(
    raw: Any, *, context: str
) -> list[dict[str, str]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ConsoleProtocolError(f"{context}: columns must be an array")
    if not raw:
        raise ConsoleProtocolError(f"{context}: columns must not be empty when set")
    if len(raw) > MAX_COLUMNS:
        raise ConsoleProtocolError(
            f"{context}: at most {MAX_COLUMNS} columns"
        )
    columns: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConsoleProtocolError(f"{context}: columns items must be objects")
        key = item.get("key")
        label = item.get("label")
        if not isinstance(key, str) or not key.strip():
            raise ConsoleProtocolError(
                f"{context}: columns[].key must be a non-empty string"
            )
        if not isinstance(label, str) or not label.strip():
            raise ConsoleProtocolError(
                f"{context}: columns[].label must be a non-empty string"
            )
        columns.append({"key": key.strip(), "label": label.strip()})
    return columns


def validate_action(raw: Any) -> dict[str, Any]:
    """Normalize and validate one ConsoleAction. Raises ConsoleProtocolError."""
    if not isinstance(raw, dict):
        raise ConsoleProtocolError("each action must be a JSON object")

    action_type = raw.get("type")
    if not isinstance(action_type, str) or action_type.strip() not in ACTION_TYPES:
        raise ConsoleProtocolError(
            f"unknown or missing type {action_type!r}; "
            f"expected one of {sorted(ACTION_TYPES)}"
        )
    action_type = action_type.strip()

    if action_type == "set_theme":
        theme = _require_str(raw, "theme").lower()
        if theme not in THEMES:
            raise ConsoleProtocolError(
                f"set_theme.theme must be one of {sorted(THEMES)}"
            )
        return {"type": "set_theme", "theme": theme}

    if action_type == "navigate":
        path = _require_str(raw, "path")
        if not path.startswith("/"):
            raise ConsoleProtocolError("navigate.path must start with '/'")
        out: dict[str, Any] = {"type": "navigate", "path": path}
        hash_value = _optional_str(raw, "hash")
        if hash_value is not None:
            out["hash"] = hash_value.lstrip("#")
        replace = _optional_bool(raw, "replace")
        if replace is not None:
            out["replace"] = replace
        return out

    if action_type == "open_create":
        resource = _require_str(raw, "resource").lower()
        if resource not in CREATE_RESOURCES:
            raise ConsoleProtocolError(
                f"open_create.resource must be one of {sorted(CREATE_RESOURCES)}"
            )
        out = {"type": "open_create", "resource": resource}
        project_id = _optional_str(raw, "projectId")
        if project_id is not None:
            out["projectId"] = project_id
        return out

    if action_type == "open_dialog":
        dialog = _require_str(raw, "dialog").lower()
        if dialog not in DIALOGS:
            raise ConsoleProtocolError(
                f"open_dialog.dialog must be one of {sorted(DIALOGS)}"
            )
        out = {"type": "open_dialog", "dialog": dialog}
        project_id = _optional_str(raw, "projectId")
        if project_id is not None:
            out["projectId"] = project_id
        return out

    if action_type == "toast":
        level = _require_str(raw, "level").lower()
        if level not in TOAST_LEVELS:
            raise ConsoleProtocolError(
                f"toast.level must be one of {sorted(TOAST_LEVELS)}"
            )
        out = {
            "type": "toast",
            "level": level,
            "message": _require_str(raw, "message"),
        }
        description = _optional_str(raw, "description")
        if description is not None:
            out["description"] = description
        return out

    if action_type == "show_pane":
        content = _require_str(raw, "content").lower()
        if content not in PANE_CONTENTS:
            raise ConsoleProtocolError(
                f"show_pane.content must be one of {sorted(PANE_CONTENTS)}"
            )
        return {"type": "show_pane", "content": content}

    if action_type == "toggle_terminal":
        return {"type": "toggle_terminal"}

    if action_type == "scroll_to_card":
        card_id = _require_str(raw, "cardId")
        return {"type": "scroll_to_card", "cardId": card_id.removeprefix("card-")}

    if action_type == "resource":
        mutation = _require_str(raw, "mutation").lower()
        if mutation not in MUTATIONS:
            raise ConsoleProtocolError(
                f"resource.mutation must be one of {sorted(MUTATIONS)}"
            )
        item = _validate_resource_item(raw, context="resource")
        out = {
            "type": "resource",
            "mutation": mutation,
            "resourceType": _require_str(raw, "resourceType").lower(),
            **item,
        }
        return out

    if action_type == "resource_list":
        items_raw = raw.get("items")
        if not isinstance(items_raw, list):
            raise ConsoleProtocolError("resource_list.items must be an array")
        if len(items_raw) > MAX_LIST_ITEMS:
            raise ConsoleProtocolError(
                f"resource_list.items: at most {MAX_LIST_ITEMS} items per list"
            )
        items = [
            _validate_resource_item(item, context=f"resource_list.items[{idx}]")
            for idx, item in enumerate(items_raw)
        ]
        out = {
            "type": "resource_list",
            "resourceType": _require_str(raw, "resourceType").lower(),
            "items": items,
        }
        for key in ("title", "description", "listHref", "emptyMessage"):
            value = _optional_str(raw, key)
            if value is not None:
                out[key] = value
        total = _optional_non_neg_int(raw, "total")
        if total is not None:
            out["total"] = total
        elif items:
            out["total"] = len(items)
        project_id = _optional_str(raw, "projectId")
        if project_id is not None:
            out["projectId"] = project_id
        columns = _validate_columns(raw.get("columns"), context="resource_list")
        if columns is not None:
            out["columns"] = columns
        return out

    if action_type == "chart":
        return _validate_chart_action(raw)

    if action_type == "refresh":
        scopes = raw.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            raise ConsoleProtocolError(
                "refresh.scopes must be a non-empty array of strings"
            )
        cleaned: list[str] = []
        for scope in scopes:
            if not isinstance(scope, str) or not scope.strip():
                raise ConsoleProtocolError(
                    "refresh.scopes entries must be non-empty strings"
                )
            cleaned.append(scope.strip().lower())
        return {"type": "refresh", "scopes": cleaned}

    raise ConsoleProtocolError(f"unhandled type {action_type!r}")


def _validate_chart_point(raw: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConsoleProtocolError(f"{context} must be an object")
    time = raw.get("time")
    if not isinstance(time, str) or not time.strip():
        raise ConsoleProtocolError(f"{context}.time must be a non-empty string")
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsoleProtocolError(f"{context}.value must be a number")
    out: dict[str, Any] = {"time": time.strip(), "value": float(value)}
    label = raw.get("label")
    if label is not None:
        if not isinstance(label, str):
            raise ConsoleProtocolError(f"{context}.label must be a string")
        text = label.strip()
        if text:
            out["label"] = text
    return out


def _validate_chart_metric(raw: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConsoleProtocolError(f"{context} must be an object")
    metric = raw.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        raise ConsoleProtocolError(f"{context}.metric must be a non-empty string")
    points_raw = raw.get("points")
    if not isinstance(points_raw, list):
        raise ConsoleProtocolError(f"{context}.points must be an array")
    if len(points_raw) > MAX_CHART_POINTS:
        raise ConsoleProtocolError(
            f"{context}.points: at most {MAX_CHART_POINTS} points"
        )
    points = [
        _validate_chart_point(point, context=f"{context}.points[{idx}]")
        for idx, point in enumerate(points_raw)
    ]
    return {"metric": metric.strip(), "points": points}


def _validate_chart_action(raw: dict[str, Any]) -> dict[str, Any]:
    metrics_raw = raw.get("metrics")
    if not isinstance(metrics_raw, list) or not metrics_raw:
        raise ConsoleProtocolError(
            "chart.metrics must be a non-empty array"
        )
    if len(metrics_raw) > MAX_CHART_METRICS:
        raise ConsoleProtocolError(
            f"chart.metrics: at most {MAX_CHART_METRICS} series"
        )
    metrics = [
        _validate_chart_metric(item, context=f"chart.metrics[{idx}]")
        for idx, item in enumerate(metrics_raw)
    ]
    out: dict[str, Any] = {
        "type": "chart",
        "title": _require_str(raw, "title"),
        "metrics": metrics,
    }
    for key in ("description", "unitLabel", "interval", "startAt", "endAt", "href", "projectId"):
        value = _optional_str(raw, key)
        if value is not None:
            out[key] = value
    chart_type = _optional_str(raw, "chartType")
    if chart_type is not None:
        normalized = chart_type.lower()
        if normalized not in CHART_TYPES:
            raise ConsoleProtocolError(
                f"chart.chartType must be one of {sorted(CHART_TYPES)}"
            )
        out["chartType"] = normalized
    kind = _optional_str(raw, "kind")
    if kind is not None:
        normalized = kind.lower()
        if normalized not in CHART_KINDS:
            raise ConsoleProtocolError(
                f"chart.kind must be one of {sorted(CHART_KINDS)}"
            )
        out["kind"] = normalized
    axis_format = _optional_str(raw, "axisFormat")
    if axis_format is not None:
        normalized = axis_format.lower()
        if normalized not in CHART_AXIS_FORMATS:
            raise ConsoleProtocolError(
                f"chart.axisFormat must be one of {sorted(CHART_AXIS_FORMATS)}"
            )
        out["axisFormat"] = normalized
    change = raw.get("changePercent")
    if change is not None:
        if isinstance(change, bool) or not isinstance(change, (int, float)):
            raise ConsoleProtocolError("chart.changePercent must be a number")
        out["changePercent"] = float(change)
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
            raise ConsoleProtocolError("actions is empty")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConsoleProtocolError(f"actions must be valid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            raw_list = [parsed]
        elif isinstance(parsed, list):
            raw_list = parsed
        else:
            raise ConsoleProtocolError(
                "actions JSON must be an object or an array of objects"
            )
    else:
        raise ConsoleProtocolError("actions must be a JSON string, object, or array")

    if not raw_list:
        raise ConsoleProtocolError("actions array is empty")
    if len(raw_list) > MAX_ACTIONS:
        raise ConsoleProtocolError(f"at most {MAX_ACTIONS} console actions per call")

    return [validate_action(item) for item in raw_list]


def build_envelope(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "actions": actions,
    }


def emit_console_actions(actions: str | list | dict) -> str:
    """Validate actions and return the canonical JSON envelope string."""
    validated = parse_actions_arg(actions)
    envelope = build_envelope(validated)
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
