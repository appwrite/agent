"""Request-scoped turn context (attachments / skill loads for this call only)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_attachments: ContextVar[list[dict[str, Any]]] = ContextVar(
    "turn_attachments", default=[]
)
_loaded_skills: ContextVar[set[str] | None] = ContextVar(
    "turn_loaded_skills", default=None
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


def begin_turn_skills() -> None:
    """Reset the per-turn skill-load set (call once at the start of a turn)."""
    _loaded_skills.set(set())


def skill_already_loaded(name: str) -> bool:
    loaded = _loaded_skills.get()
    return bool(loaded and name in loaded)


def mark_skill_loaded(name: str) -> None:
    loaded = _loaded_skills.get()
    if loaded is None:
        loaded = set()
        _loaded_skills.set(loaded)
    loaded.add(name)
