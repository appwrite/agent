"""Request-scoped turn context (attachments available to tools for this call only)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_attachments: ContextVar[list[dict[str, Any]]] = ContextVar(
    "turn_attachments", default=[]
)


def set_turn_attachments(attachments: list[dict[str, Any]] | None) -> None:
    _attachments.set(list(attachments or []))


def get_turn_attachments() -> list[dict[str, Any]]:
    return list(_attachments.get())


def find_turn_attachment(attachment_id: str) -> dict[str, Any] | None:
    for att in get_turn_attachments():
        if str(att.get("id")) == attachment_id or str(att.get("name")) == attachment_id:
            return att
    return None
