"""Normalize model message content to plain text for user-facing fields.

Responses API / multimodal models often return content as a list of blocks:
  [{"type": "text", "text": "...", "index": 0}]

Persisting ``str(blocks)`` yields a Python repr (single quotes, escaped ``\\n``)
that breaks markdown rendering. Always run content through ``content_to_text``
before writing contentText, titles, or summaries.
"""

from __future__ import annotations

import json
from typing import Any


def content_to_text(content: Any) -> str:
    """Extract plain text from string, content-block list/dict, or message-like objects."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            part = _block_to_text(block)
            if part:
                parts.append(part)
        return "".join(parts)
    if isinstance(content, dict):
        return _block_to_text(content)
    # LangChain messages / chunk wrappers
    if hasattr(content, "content") and not isinstance(content, type):
        inner = getattr(content, "content", None)
        if inner is not content:
            return content_to_text(inner)
    # Typed content-block objects (e.g. with .type / .text)
    if hasattr(content, "text") and getattr(content, "type", "text") in (None, "text"):
        return str(getattr(content, "text") or "")
    # Scalars only — never str(list/dict) into user-facing fields (handled above).
    if isinstance(content, (int, float, bool)):
        return str(content)
    return ""


def _block_to_text(block: Any) -> str:
    if block is None:
        return ""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        block_type = block.get("type", "text")
        if block_type in (None, "text", "output_text"):
            return str(block.get("text") or "")
        # Skip reasoning / tool / image blocks for user-facing markdown.
        return ""
    block_type = getattr(block, "type", None)
    if block_type in (None, "text", "output_text") and hasattr(block, "text"):
        return str(getattr(block, "text") or "")
    return ""


def structured_to_json_text(value: Any) -> str:
    """Serialize tool args/results as JSON (true/false), not Python repr (True/False)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        try:
            return json.dumps(value, default=str, ensure_ascii=False)
        except TypeError:
            return ""
    if hasattr(value, "content"):
        return content_to_text(value)
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except TypeError:
        return content_to_text(value)
