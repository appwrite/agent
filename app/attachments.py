"""Inline chat attachments — content is supplied per request (stateless)."""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

from app.config import get_settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# Vision APIs (OpenAI / compatible) only accept these image MIME types.
# Do not treat other image/* types (SVG, BMP, TIFF, HEIC, …) as vision images.
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
        "text/typescript",
        "text/x-python",
        "text/x-script.python",
        "text/x-java-source",
        "text/x-c",
        "text/x-c++src",
        "text/x-golang",
        "text/x-ruby",
        "text/x-rust",
        "text/x-php",
        "text/x-shellscript",
        "text/x-sql",
        "text/tab-separated-values",
        "text/xml",
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/xhtml+xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
        "application/typescript",
        "application/sql",
        "application/graphql",
        "application/x-sh",
        "application/x-httpd-php",
        "image/svg+xml",
    }
)
TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".mdx",
        ".rst",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".jsonc",
        ".yaml",
        ".yml",
        ".xml",
        ".svg",
        ".html",
        ".htm",
        ".xhtml",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".astro",
        ".py",
        ".pyi",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".swift",
        ".php",
        ".phtml",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".cmd",
        ".env",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".config",
        ".properties",
        ".log",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".cs",
        ".fs",
        ".fsx",
        ".dart",
        ".lua",
        ".pl",
        ".pm",
        ".r",
        ".rmd",
        ".jl",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".clj",
        ".cljs",
        ".scala",
        ".sc",
        ".groovy",
        ".gradle",
        ".m",
        ".mm",
        ".zig",
        ".nim",
        ".v",
        ".vb",
        ".tf",
        ".hcl",
        ".graphql",
        ".gql",
        ".proto",
        ".prisma",
        ".dockerfile",
        ".editorconfig",
        ".gitignore",
        ".gitattributes",
        ".dockerignore",
        ".npmrc",
        ".nvmrc",
        ".eslintrc",
        ".prettierrc",
        ".babelrc",
        ".lock",
        ".plist",
    }
)

# Suffixes that look text-ish by name but are binary — never inline as text.
BINARY_EXTENSIONS = frozenset(
    {
        ".wasm",
        ".svgz",  # gzip-compressed SVG
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
    """True only for vision-API-compatible image MIME types."""
    return mime.split(";")[0].strip().lower() in IMAGE_TYPES


def is_text_like(mime: str, filename: str) -> bool:
    mime = mime.split(";")[0].strip().lower()
    suffix = Path(filename).suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return False
    if mime in TEXT_TYPES or mime.startswith("text/"):
        return True
    # SVG often arrives as application/octet-stream from some browsers.
    if suffix == ".svg":
        return True
    return suffix in TEXT_EXTENSIONS and suffix not in BINARY_EXTENSIONS


def resolve_kind(mime: str, filename: str, hint: str | None = None) -> str:
    """Classify attachment for the LLM, ignoring unsafe client image hints."""
    if is_image(mime):
        return "image"
    if is_text_like(mime, filename):
        return "text"
    hint = (hint or "").strip().lower()
    if hint == "text":
        return "text"
    # Clients often mark any image/* (including SVG) as "image". Only honor
    # that hint when the MIME is vision-safe — already handled above.
    return "file"


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
    kind = resolve_kind(mime, name, str(att.get("kind") or "") or None)
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
        data: bytes | None = att.get("_bytes")
        if data is None and att.get("content_base64"):
            try:
                _, data = decode_attachment(att)
            except Exception:  # noqa: BLE001
                data = None
        aid = att.get("id") or name
        kind = resolve_kind(mime, str(name), str(att.get("kind") or "") or None)

        if kind == "image" and data:
            # Normalize jpeg alias for providers that reject image/jpg.
            vision_mime = "image/jpeg" if mime == "image/jpg" else mime
            b64 = base64.b64encode(data).decode("ascii")
            image_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{vision_mime};base64,{b64}"},
                }
            )
            notes.append(f"- image `{name}` (id=`{aid}`, {mime}, {size} bytes)")
        elif kind == "text" and data:
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
