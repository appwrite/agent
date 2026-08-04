"""Shared network helpers — HTTPS validation and SSRF host blocking."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def host_of(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.hostname


def is_blocked_host(hostname: str) -> bool:
    """Block obvious local/private targets (SSRF), not a public domain allowlist."""
    host = hostname.lower().strip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def validate_https_url(url: str) -> str | None:
    """Return an error string if invalid; otherwise None."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return "Error: only https:// URLs with a hostname are allowed"
    if is_blocked_host(parsed.hostname):
        return f"Error: host '{parsed.hostname}' is not allowed (local/private)"
    return None
