"""Resolve chat-turn attachments into MCP InputFile argument shapes."""

from __future__ import annotations

import base64
from typing import Any

from app.turn_context import find_turn_attachment


def file_from_turn_attachment(value: Any) -> Any:
    """Map a chat attachment id/name to MCP inline file content when possible.

    Hosted MCP rejects bare paths/ids; chat attachments already arrive on the
    turn as bytes. Resolve them here so the model can pass attachment ids
    straight into storage_create_file (and similar InputFile params).
    """
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return value

    raw = value.strip()
    if not raw or raw.startswith("{") or "://" in raw or "/" in raw:
        return value

    att = find_turn_attachment(raw)
    if not att:
        return value

    data = att.get("_bytes")
    if not isinstance(data, (bytes, bytearray)):
        return value

    out: dict[str, Any] = {
        "filename": str(att.get("name") or raw),
        "content": base64.b64encode(bytes(data)).decode("ascii"),
        "encoding": "base64",
    }
    mime = att.get("mime")
    if isinstance(mime, str) and mime.strip():
        out["mime_type"] = mime.strip()
    return out


def resolve_file_arguments(arguments: Any) -> Any:
    if not isinstance(arguments, dict) or "file" not in arguments:
        return arguments
    out = dict(arguments)
    out["file"] = file_from_turn_attachment(out["file"])
    return out
