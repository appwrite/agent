"""Inline chat attachments — content is supplied per request (stateless)."""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

from app.config import get_settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
)
TEXT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/html",
        "text/css",
        "text/javascript",
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
        "application/typescript",
    }
)
TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".py",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".php",
        ".sql",
        ".sh",
        ".env",
        ".toml",
        ".ini",
        ".cfg",
        ".log",
    }
)


def sanitize_filename(name: str) -> str:
    base = Path(name or "file").name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "file"
    return cleaned[:180]


def guess_mime(filename: str, content_type: str | None = None) -> str:
    if content_type and content_type != "application/octet-stream":
        return content_type.split(";")[0].strip().lower()
    guessed, _ = mimetypes.guess_type(filename)
    return (guessed or "application/octet-stream").lower()


def is_image(mime: str) -> bool:
    return mime in IMAGE_TYPES or mime.startswith("image/")


def is_text_like(mime: str, filename: str) -> bool:
    if mime in TEXT_TYPES or mime.startswith("text/"):
        return True
    return Path(filename).suffix.lower() in TEXT_EXTENSIONS


def decode_attachment(att: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """Normalize an inline attachment dict → (public meta, raw bytes)."""
    settings = get_settings()
    name = sanitize_filename(str(att.get("name") or "file"))
    mime = guess_mime(name, att.get("mime"))
    raw_b64 = att.get("content_base64") or att.get("data_base64") or ""
    if not raw_b64:
        raise ValueError(f"Attachment {name!r} missing content_base64")
    try:
        data = base64.b64decode(raw_b64, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Attachment {name!r} has invalid base64") from exc
    if not data:
        raise ValueError(f"Attachment {name!r} is empty")
    if len(data) > settings.attachments_max_bytes:
        raise ValueError(
            f"Attachment {name!r} exceeds max size "
            f"({settings.attachments_max_bytes} bytes)"
        )
    kind = (
        str(att.get("kind") or "")
        or (
            "image"
            if is_image(mime)
            else "text"
            if is_text_like(mime, name)
            else "file"
        )
    )
    meta = {
        "id": str(att.get("id") or name),
        "name": name,
        "mime": mime,
        "size": len(data),
        "kind": kind,
    }
    return meta, data


def normalize_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate inline attachments and attach decoded bytes under `_bytes`."""
    settings = get_settings()
    items = list(attachments or [])
    if len(items) > settings.attachments_max_per_message:
        raise ValueError(
            f"Too many attachments (max {settings.attachments_max_per_message})"
        )
    out: list[dict[str, Any]] = []
    for att in items:
        meta, data = decode_attachment(att)
        out.append({**meta, "_bytes": data})
    return out


def text_from_bytes(data: bytes, *, max_chars: int) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def build_human_content(
    message: str,
    attachments: list[dict[str, Any]] | None,
) -> str | list[dict[str, Any]]:
    """Build LangChain HumanMessage content from inline attachments."""
    text = (message or "").strip()
    notes: list[str] = []
    image_parts: list[dict[str, Any]] = []

    for att in attachments or []:
        name = att.get("name") or "file"
        mime = att.get("mime") or "application/octet-stream"
        size = int(att.get("size") or 0)
        kind = att.get("kind") or "file"
        data: bytes | None = att.get("_bytes")
        if data is None and att.get("content_base64"):
            try:
                _, data = decode_attachment(att)
            except Exception:  # noqa: BLE001
                data = None
        aid = att.get("id") or name

        if (kind == "image" or is_image(mime)) and data:
            b64 = base64.b64encode(data).decode("ascii")
            image_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
            notes.append(f"- image `{name}` (id=`{aid}`, {mime}, {size} bytes)")
        elif (kind == "text" or is_text_like(mime, name)) and data:
            body = text_from_bytes(data, max_chars=8_000)
            notes.append(
                f"- text file `{name}` (id=`{aid}`, {mime}, {size} bytes):\n"
                f"```\n{body}\n```"
            )
        else:
            notes.append(
                f"- file `{name}` (id=`{aid}`, {mime}, {size} bytes) was attached. "
                "Binary contents are available only for the current request."
            )

    if not text:
        text = "Please review the attached file(s)."
    if notes:
        text = f"{text}\n\n### Attachments\n" + "\n".join(notes)

    if not image_parts:
        return text
    return [{"type": "text", "text": text}, *image_parts]


def public_meta(att: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": att.get("id"),
        "name": att.get("name"),
        "mime": att.get("mime"),
        "size": att.get("size"),
        "kind": att.get("kind") or "file",
    }
