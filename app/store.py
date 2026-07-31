"""In-memory conversation metadata for the POC. Cloud will own persistence."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Conversation:
    id: str
    created_at: str
    title: str = "New conversation"
    message_count: int = 0
    last_response: str = ""
    events: list[dict] = field(default_factory=list)


class ConversationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Conversation] = {}

    def create(self, title: str | None = None) -> Conversation:
        conv = Conversation(
            id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            title=title or "New conversation",
        )
        with self._lock:
            self._items[conv.id] = conv
        return conv

    def get(self, conv_id: str) -> Conversation | None:
        with self._lock:
            return self._items.get(conv_id)

    def list(self) -> list[Conversation]:
        with self._lock:
            return sorted(self._items.values(), key=lambda c: c.created_at, reverse=True)

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def append_event(
        self,
        conv_id: str,
        role: str,
        content: str,
        meta: dict | None = None,
    ) -> Conversation | None:
        with self._lock:
            conv = self._items.get(conv_id)
            if not conv:
                return None
            item = {
                "role": role,
                "content": content,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            if meta:
                item["meta"] = meta
            conv.events.append(item)
            conv.message_count = len(conv.events)
            if role == "assistant":
                conv.last_response = content
            if role == "user" and conv.title == "New conversation":
                conv.title = content[:80]
            return conv


store = ConversationStore()
