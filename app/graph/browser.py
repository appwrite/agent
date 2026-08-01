"""Headless Chromium fetch for JS-rendered pages."""

from __future__ import annotations

import atexit
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-fetch")
_LOCK = threading.RLock()
_playwright = None
_browser = None

# Internal SERP endpoint only — never surface this host in tool/UI copy.
_WEB_SEARCH_URL = "https://search.brave.com/search?q={query}"

# Soft dedupe: identical URL within a short window returns cached text
# (stops the same-page loop without blocking list→article navigation).
CACHE_TTL_S = 120.0
_cache: dict[str, tuple[float, int, str, str]] = {}
_cache_lock = threading.Lock()

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
]

_STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""

# Keep article/list links so the model can open a story after a section page.
_EXTRACT_JS = """() => {
  const junk = [
    "script", "style", "noscript", "svg", "iframe", "canvas",
    "nav", "footer", "aside",
    "[role='banner']", "[role='navigation']", "[role='complementary']",
    "[aria-hidden='true']",
  ].join(", ");
  document.querySelectorAll(junk).forEach((el) => el.remove());
  const main =
    document.querySelector("main, article, [role='main'], #main-content") ||
    document.body;

  const links = [];
  const seen = new Set();
  for (const a of main.querySelectorAll("a[href]")) {
    const href = a.href || "";
    if (!href.startsWith("http")) continue;
    if (seen.has(href)) continue;
    const label = (a.innerText || a.getAttribute("aria-label") || "").trim();
    if (!label || label.length < 8) continue;
    // Prefer story-like paths; still keep a modest set of others.
    const interesting =
      /\\/(news|sport|article|story|world|politics|business)\\//i.test(href) ||
      label.length >= 24;
    if (!interesting) continue;
    seen.add(href);
    links.push(`- ${label.replace(/\\s+/g, " ").slice(0, 160)} → ${href}`);
    if (links.length >= 40) break;
  }

  const text = (main.innerText || "")
    .replace(/[ \\t]+/g, " ")
    .replace(/\\n{3,}/g, "\\n\\n")
    .trim();

  if (!links.length) return text;
  return `${text}\\n\\n### Links\\n${links.join("\\n")}`;
}"""


def _close_browser() -> None:
    global _playwright, _browser
    with _LOCK:
        if _browser is not None:
            try:
                _browser.close()
            except Exception:  # noqa: BLE001
                pass
            _browser = None
        if _playwright is not None:
            try:
                _playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            _playwright = None


atexit.register(_close_browser)


def _ensure_playwright():
    global _playwright
    with _LOCK:
        if _playwright is None:
            _playwright = sync_playwright().start()
        return _playwright


def _ensure_browser():
    global _browser
    with _LOCK:
        if _browser is not None and _browser.is_connected():
            return _browser
        pw = _ensure_playwright()
        if _browser is not None:
            try:
                _browser.close()
            except Exception:  # noqa: BLE001
                pass
            _browser = None
        _browser = pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        return _browser


def _new_context(browser, *, viewport: dict | None = None):
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        viewport=viewport or {"width": 1280, "height": 2200},
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
        java_script_enabled=True,
        ignore_https_errors=False,
    )
    context.add_init_script(_STEALTH_INIT)
    return context


def _fetch_sync(url: str, timeout_ms: int) -> tuple[int, str, str]:
    """Return (status, final_url, text). Runs on the dedicated browser thread."""
    browser = _ensure_browser()
    context = _new_context(browser)
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(12_000, timeout_ms))
        except PlaywrightTimeoutError:
            pass
        # Give client-rendered shells a beat to paint.
        page.wait_for_timeout(800)
        text = page.evaluate(_EXTRACT_JS) or ""
        status = response.status if response is not None else 0
        final_url = page.url
        return status, final_url, text
    finally:
        context.close()


def browser_fetch_text(url: str, *, timeout_ms: int = 35_000) -> tuple[int, str, str]:
    """Fetch a URL in headless Chromium and return rendered text.

    Playwright sync API must not share the FastAPI event-loop thread, so work
    runs on a dedicated executor thread with a reused browser process.
    """
    cache_key = url.strip()
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1], hit[2], hit[3]

    future = _POOL.submit(_fetch_sync, url, timeout_ms)
    try:
        status, final_url, text = future.result(timeout=(timeout_ms / 1000.0) + 15)
    except Exception:
        # Browser may be wedged after a hard failure — recycle next call.
        logger.exception("browser_fetch failed for %s", url)
        _POOL.submit(_close_browser).result(timeout=10)
        raise

    with _cache_lock:
        _cache[cache_key] = (time.monotonic(), status, final_url, text)
        # Bound memory.
        if len(_cache) > 64:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:16]
            for key, _ in oldest:
                _cache.pop(key, None)
    return status, final_url, text


def host_of(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    return parsed.hostname.lower()


_WEB_SEARCH_EXTRACT_JS = """(maxResults) => {
  const blocked =
    /unusual traffic|not a robot|enable javascript|detected unusual|captcha/i.test(
      document.body?.innerText || ""
    );
  if (blocked) {
    return { blocked: true, results: [] };
  }

  const results = [];
  const seen = new Set();
  const anchors = document.querySelectorAll(
    'div[data-type="web"] a[href^="http"], main a[href^="http"]'
  );
  for (const a of anchors) {
    const href = a.href || "";
    const title = (a.innerText || "").trim();
    if (!href.startsWith("http") || title.length < 8 || seen.has(href)) continue;
    // Skip SERP chrome / same-host nav links.
    try {
      if (new URL(href).hostname === location.hostname) continue;
    } catch (_) {
      continue;
    }
    const root = a.closest("div") || a.parentElement;
    const snip = (root?.innerText || "")
      .replace(title, "")
      .replace(/\\s+/g, " ")
      .trim()
      .slice(0, 320);
    seen.add(href);
    results.push({ title: title.slice(0, 180), url: href, snippet: snip });
    if (results.length >= maxResults) break;
  }
  return { blocked: false, results };
}"""


def _web_search_sync(query: str, *, timeout_ms: int, max_results: int) -> dict:
    browser = _ensure_browser()
    context = _new_context(browser)
    page = context.new_page()
    try:
        url = _WEB_SEARCH_URL.format(query=quote_plus(query))
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(8_000, timeout_ms))
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(700)
        data = page.evaluate(_WEB_SEARCH_EXTRACT_JS, max_results) or {}
        results = data.get("results") or []
        blocked = bool(data.get("blocked")) or not results
        return {
            "status": response.status if response is not None else 0,
            "blocked": blocked and not results,
            "query": query,
            "results": results[:max_results],
        }
    finally:
        context.close()


def web_search_results(
    query: str,
    *,
    max_results: int = 8,
    timeout_ms: int = 35_000,
) -> dict:
    """Run a headless-browser web search (no Search API key)."""
    q = " ".join((query or "").split())
    if not q:
        raise ValueError("query is empty")
    max_results = max(1, min(int(max_results), 10))

    future = _POOL.submit(
        _web_search_sync, q, timeout_ms=timeout_ms, max_results=max_results
    )
    try:
        return future.result(timeout=(timeout_ms / 1000.0) + 45)
    except Exception:
        logger.exception("web_search failed for %r", q)
        _POOL.submit(_close_browser).result(timeout=10)
        raise
