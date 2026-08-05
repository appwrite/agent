"""Agent abuse-report protocol — agent → Cloud threat envelopes.

The `report_threat` tool does not talk to the database. It validates a
structured report and returns a canonical envelope. Cloud creates a
`threats` document (and alerts) when it sees a successful `tool_end`.

Wire format version: appwrite.threat/v1
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL = "appwrite.threat/v1"

CATEGORIES = frozenset({"illegal", "immoral"})
MAX_SUMMARY = 280
MAX_DETAILS = 4000
MAX_QUOTE = 1000


class ThreatProtocolError(ValueError):
    """Invalid threat report payload."""


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ThreatProtocolError(f"{key} must be a string")
    text = value.strip()
    if not text:
        raise ThreatProtocolError(f"{key} is required")
    return text


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ThreatProtocolError(f"{key} must be a string")
    text = value.strip()
    return text or None


def validate_report(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ThreatProtocolError("report must be an object")

    category = _require_str(raw, "category").lower()
    if category not in CATEGORIES:
        raise ThreatProtocolError(
            f"category must be one of {sorted(CATEGORIES)}; got {category!r}"
        )

    summary = _require_str(raw, "summary")
    if len(summary) > MAX_SUMMARY:
        raise ThreatProtocolError(f"summary exceeds {MAX_SUMMARY} characters")

    details = _require_str(raw, "details")
    if len(details) > MAX_DETAILS:
        raise ThreatProtocolError(f"details exceeds {MAX_DETAILS} characters")

    out: dict[str, Any] = {
        "category": category,
        "summary": summary,
        "details": details,
    }

    quote = _optional_str(raw, "quote")
    if quote is not None:
        if len(quote) > MAX_QUOTE:
            raise ThreatProtocolError(f"quote exceeds {MAX_QUOTE} characters")
        out["quote"] = quote

    return out


def parse_report_arg(report: str | dict) -> dict[str, Any]:
    """Accept a JSON string or a report object."""
    if isinstance(report, dict):
        return validate_report(report)
    if not isinstance(report, str):
        raise ThreatProtocolError("report must be a JSON string or object")

    text = report.strip()
    if not text:
        raise ThreatProtocolError("report is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ThreatProtocolError(f"report must be valid JSON: {exc}") from exc
    return validate_report(parsed)


def build_envelope(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        **report,
    }


def emit_threat_report(
    category: str,
    summary: str,
    details: str,
    quote: str | None = None,
) -> str:
    """Validate fields and return the canonical JSON envelope string."""
    payload: dict[str, Any] = {
        "category": category,
        "summary": summary,
        "details": details,
    }
    if quote is not None:
        payload["quote"] = quote
    envelope = build_envelope(validate_report(payload))
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
