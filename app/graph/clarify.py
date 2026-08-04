"""Agent clarify protocol — structured follow-ups for the Console.

The `clarify` tool does not answer the user in chat. It posts prompts that the
Console renders as choices, confirmations, or text inputs. The user's next
message (or structured reply) carries the answers.

Wire format version: appwrite.clarify/v1
Full contract: docs/clarify-protocol.md
"""

from __future__ import annotations

import json
import re
from typing import Any

PROTOCOL = "appwrite.clarify/v1"
PROTOCOL_DOC = "docs/clarify-protocol.md"

KINDS = frozenset({"choice", "confirm", "text"})
# 1–64 chars: leading latin letter, then optional alnum / _ . -
ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")

MAX_PROMPTS = 5
MAX_OPTIONS = 12
MAX_QUESTION = 500
MAX_HINT = 500
MAX_LABEL = 120
MAX_DESCRIPTION = 300
MAX_PLACEHOLDER = 200
MAX_DEFAULT = 2000
MAX_TITLE = 120


class ClarifyProtocolError(ValueError):
    """Invalid clarify prompt payload."""


def _require_str(obj: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ClarifyProtocolError(f"{obj.get('kind') or obj.get('id')!r}: {key} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise ClarifyProtocolError(f"{obj.get('kind') or obj.get('id')!r}: {key} is required")
    return text


def _optional_str(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ClarifyProtocolError(f"{obj.get('kind') or obj.get('id')!r}: {key} must be a string")
    text = value.strip()
    return text or None


def _optional_bool(obj: dict[str, Any], key: str) -> bool | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ClarifyProtocolError(f"{obj.get('kind') or obj.get('id')!r}: {key} must be a boolean")
    return value


def _require_id(raw: Any, *, context: str) -> str:
    if not isinstance(raw, str) or not ID_RE.match(raw.strip()):
        raise ClarifyProtocolError(
            f"{context}: id must match ^[a-z][a-z0-9_.-]{{0,63}}$ (got {raw!r})"
        )
    return raw.strip()


def _validate_option(raw: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ClarifyProtocolError(f"{context}: option must be an object")
    out: dict[str, Any] = {
        "id": _require_id(raw.get("id"), context=f"{context}.id"),
        "label": _require_str(raw, "label"),
    }
    if len(out["label"]) > MAX_LABEL:
        raise ClarifyProtocolError(f"{context}: label exceeds {MAX_LABEL} characters")
    description = _optional_str(raw, "description")
    if description is not None:
        if len(description) > MAX_DESCRIPTION:
            raise ClarifyProtocolError(
                f"{context}: description exceeds {MAX_DESCRIPTION} characters"
            )
        out["description"] = description
    return out


def validate_prompt(raw: Any) -> dict[str, Any]:
    """Normalize and validate one ClarifyPrompt. Raises ClarifyProtocolError."""
    if not isinstance(raw, dict):
        raise ClarifyProtocolError("each prompt must be a JSON object")

    kind = raw.get("kind")
    if not isinstance(kind, str) or kind.strip() not in KINDS:
        raise ClarifyProtocolError(
            f"unknown or missing kind {kind!r}; expected one of {sorted(KINDS)}"
        )
    kind = kind.strip()

    prompt_id = _require_id(raw.get("id"), context="prompt")
    question = _require_str(raw, "question")
    if len(question) > MAX_QUESTION:
        raise ClarifyProtocolError(f"question exceeds {MAX_QUESTION} characters")

    out: dict[str, Any] = {
        "id": prompt_id,
        "kind": kind,
        "question": question,
    }

    hint = _optional_str(raw, "hint")
    if hint is not None:
        if len(hint) > MAX_HINT:
            raise ClarifyProtocolError(f"hint exceeds {MAX_HINT} characters")
        out["hint"] = hint

    required = _optional_bool(raw, "required")
    if required is not None:
        out["required"] = required
    else:
        out["required"] = True

    if kind == "choice":
        options_raw = raw.get("options")
        if not isinstance(options_raw, list) or len(options_raw) < 2:
            raise ClarifyProtocolError("choice.options must be an array with at least 2 items")
        if len(options_raw) > MAX_OPTIONS:
            raise ClarifyProtocolError(f"choice.options: at most {MAX_OPTIONS} options")
        options = [
            _validate_option(item, context=f"choice.options[{idx}]")
            for idx, item in enumerate(options_raw)
        ]
        seen: set[str] = set()
        for opt in options:
            if opt["id"] in seen:
                raise ClarifyProtocolError(f"choice.options: duplicate id {opt['id']!r}")
            seen.add(opt["id"])
        out["options"] = options
        allow_multiple = _optional_bool(raw, "allowMultiple")
        if allow_multiple is not None:
            out["allowMultiple"] = allow_multiple
        else:
            out["allowMultiple"] = False
        return out

    if kind == "confirm":
        confirm_label = _optional_str(raw, "confirmLabel") or "Confirm"
        cancel_label = _optional_str(raw, "cancelLabel") or "Cancel"
        if len(confirm_label) > MAX_LABEL or len(cancel_label) > MAX_LABEL:
            raise ClarifyProtocolError(f"confirm labels exceed {MAX_LABEL} characters")
        out["confirmLabel"] = confirm_label
        out["cancelLabel"] = cancel_label
        danger = _optional_bool(raw, "danger")
        if danger is not None:
            out["danger"] = danger
        else:
            out["danger"] = False
        return out

    # text
    placeholder = _optional_str(raw, "placeholder")
    if placeholder is not None:
        if len(placeholder) > MAX_PLACEHOLDER:
            raise ClarifyProtocolError(
                f"placeholder exceeds {MAX_PLACEHOLDER} characters"
            )
        out["placeholder"] = placeholder
    default_value = _optional_str(raw, "defaultValue")
    if default_value is not None:
        if len(default_value) > MAX_DEFAULT:
            raise ClarifyProtocolError(
                f"defaultValue exceeds {MAX_DEFAULT} characters"
            )
        out["defaultValue"] = default_value
    multiline = _optional_bool(raw, "multiline")
    if multiline is not None:
        out["multiline"] = multiline
    else:
        out["multiline"] = False
    return out


def parse_prompts_arg(prompts: str | list | dict) -> list[dict[str, Any]]:
    """Accept a JSON string, a single prompt object, or an array of prompts."""
    if isinstance(prompts, dict):
        raw_list: list[Any] = [prompts]
    elif isinstance(prompts, list):
        raw_list = prompts
    elif isinstance(prompts, str):
        text = prompts.strip()
        if not text:
            raise ClarifyProtocolError("prompts is empty")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ClarifyProtocolError(f"prompts must be valid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            raw_list = [parsed]
        elif isinstance(parsed, list):
            raw_list = parsed
        else:
            raise ClarifyProtocolError(
                "prompts JSON must be an object or an array of objects"
            )
    else:
        raise ClarifyProtocolError("prompts must be a JSON string, object, or array")

    if not raw_list:
        raise ClarifyProtocolError("prompts array is empty")
    if len(raw_list) > MAX_PROMPTS:
        raise ClarifyProtocolError(f"at most {MAX_PROMPTS} clarify prompts per call")

    validated = [validate_prompt(item) for item in raw_list]
    seen_ids: set[str] = set()
    for prompt in validated:
        if prompt["id"] in seen_ids:
            raise ClarifyProtocolError(f"duplicate prompt id {prompt['id']!r}")
        seen_ids.add(prompt["id"])
    return validated


def build_envelope(
    prompts: list[dict[str, Any]],
    *,
    title: str | None = None,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "protocol": PROTOCOL,
        "prompts": prompts,
    }
    if title:
        envelope["title"] = title
    return envelope


def emit_clarify_prompts(
    prompts: str | list | dict,
    *,
    title: str | None = None,
) -> str:
    """Validate prompts and return the canonical JSON envelope string."""
    validated = parse_prompts_arg(prompts)
    clean_title: str | None = None
    if title is not None:
        text = title.strip()
        if text:
            if len(text) > MAX_TITLE:
                raise ClarifyProtocolError(f"title exceeds {MAX_TITLE} characters")
            clean_title = text
    envelope = build_envelope(validated, title=clean_title)
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
