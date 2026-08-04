"""Lightweight HTTPS GET for JSON/text — no browser, SSRF-safe."""

from __future__ import annotations

import logging
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.graph.net import host_of, is_blocked_host, validate_https_url

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 15.0
DEFAULT_MAX_BYTES = 200_000
USER_AGENT = "AppwriteAgent/1.0 (+https://appwrite.io; http_get)"

# Content types we will return as text. Everything else gets a short notice.
_TEXTISH_PREFIXES = (
    "text/",
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/javascript",
    "application/xhtml+xml",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/sql",
    "application/graphql",
)


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only to public https hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        err = validate_https_url(newurl)
        if err:
            raise URLError(f"redirect blocked: {err}")
        hostname = host_of(newurl)
        if hostname and is_blocked_host(hostname):
            raise URLError(f"redirect landed on blocked host '{hostname}'")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _content_type_of(headers) -> str:  # noqa: ANN001
    raw = headers.get("Content-Type") or headers.get("content-type") or ""
    if not raw:
        return ""
    msg = EmailMessage()
    msg["content-type"] = raw
    return (msg.get_content_type() or raw.split(";")[0].strip()).lower()


def _charset_of(headers) -> str:  # noqa: ANN001
    raw = headers.get("Content-Type") or headers.get("content-type") or ""
    if not raw:
        return "utf-8"
    msg = EmailMessage()
    msg["content-type"] = raw
    charset = msg.get_content_charset()
    return charset or "utf-8"


def _is_textish(content_type: str) -> bool:
    if not content_type:
        # Unknown — allow and let decode fail softly.
        return True
    ct = content_type.lower()
    return any(ct == p or ct.startswith(p) for p in _TEXTISH_PREFIXES)


def http_get_text(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple[int, str, str, str]:
    """GET a public HTTPS URL and return (status, final_url, content_type, body).

    Raises ValueError for validation / policy errors; other network errors
    propagate as URLError / TimeoutError / OSError.
    """
    err = validate_https_url(url)
    if err:
        raise ValueError(err.removeprefix("Error: ").strip() or err)

    limit = max(1_000, min(int(max_bytes), 500_000))
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, text/*, */*",
        },
        method="GET",
    )
    opener = build_opener(_SafeRedirectHandler())

    try:
        with opener.open(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", None) or resp.getcode() or 0
            final_url = resp.geturl() or url
            headers = resp.headers
            content_type = _content_type_of(headers)
            raw = resp.read(limit + 1)
    except HTTPError as exc:
        status = exc.code or 0
        final_url = exc.url if getattr(exc, "url", None) else url
        headers = exc.headers or {}
        content_type = _content_type_of(headers)
        try:
            raw = exc.read(limit + 1) if exc.fp else b""
        except Exception:  # noqa: BLE001
            raw = b""

    final_host = urlparse(final_url).hostname
    if final_host and is_blocked_host(final_host):
        raise ValueError(f"redirect landed on blocked host '{final_host}'")

    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]

    if not _is_textish(content_type):
        return (
            int(status),
            final_url,
            content_type,
            (
                f"(binary or non-text content-type {content_type!r}; "
                f"{len(raw)} bytes not returned as text. "
                "Use browser_fetch for HTML pages if you need rendered content.)"
            ),
        )

    charset = _charset_of(headers)
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")

    if truncated:
        text += f"\n\n… truncated at {limit} bytes"

    return int(status), final_url, content_type, text
