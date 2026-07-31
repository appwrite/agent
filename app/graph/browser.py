"""Headless Chromium fetch for JS-rendered pages."""

from __future__ import annotations

import atexit
import base64
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-fetch")
_LOCK = threading.RLock()
_playwright = None
_browser = None
_search_browser = None

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
    global _playwright, _browser, _search_browser
    with _LOCK:
        for attr in ("_browser", "_search_browser"):
            browser = globals().get(attr)
            if browser is not None:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
                globals()[attr] = None
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


def _ensure_search_browser():
    """Prefer Firefox for search — Chromium datacenter fingerprints get CAPTCHA'd."""
    global _search_browser
    with _LOCK:
        if _search_browser is not None and _search_browser.is_connected():
            return _search_browser, "firefox"
        pw = _ensure_playwright()
        if _search_browser is not None:
            try:
                _search_browser.close()
            except Exception:  # noqa: BLE001
                pass
            _search_browser = None
        try:
            _search_browser = pw.firefox.launch(headless=True)
            return _search_browser, "firefox"
        except Exception:  # noqa: BLE001
            logger.exception("firefox launch failed; falling back to chromium")
            return _ensure_browser(), "chromium"


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


_GOOGLE_EXTRACT_JS = """() => {
  const blocked =
    /unusual traffic|not a robot|enable javascript|detected unusual/i.test(
      document.body?.innerText || ""
    ) || !!document.querySelector("#captcha-form, #recaptcha, form#captcha");
  if (blocked) {
    return { blocked: true, results: [], finalUrl: location.href };
  }

  const results = [];
  const seen = new Set();
  const nodes = document.querySelectorAll("#search a h3, #rso a h3, #main a h3");
  for (const h3 of nodes) {
    const a = h3.closest("a");
    if (!a) continue;
    let href = a.href || "";
    if (!href.startsWith("http")) continue;
    if (/google\\./i.test(href) && !/\\/url\\?/.test(href)) continue;
    // Unwrap google redirect links.
    try {
      const u = new URL(href);
      if (u.hostname.includes("google.") && u.pathname === "/url") {
        href = u.searchParams.get("q") || u.searchParams.get("url") || href;
      }
    } catch (_) {}
    if (!href.startsWith("http")) continue;
    if (seen.has(href)) continue;
    const title = (h3.innerText || "").trim();
    if (!title || title.length < 2) continue;
    const root =
      a.closest("div.g") ||
      a.closest("div[data-sokoban-container]") ||
      a.closest("div[jscontroller]") ||
      a.parentElement;
    const snippetEl = root
      ? root.querySelector("[data-sncf], .VwiC3b, .IsZvec, .aCOpRe, .yXK7lf")
      : null;
    const snippet = (snippetEl?.innerText || "").replace(/\\s+/g, " ").trim();
    seen.add(href);
    results.push({ title, url: href, snippet: snippet.slice(0, 320) });
    if (results.length >= 10) break;
  }
  return { blocked: false, results, finalUrl: location.href };
}"""


def _dismiss_google_consent(page) -> None:
    selectors = [
        "button#L2AGLb",
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
        'button:has-text("Accept All")',
        'div[role="button"]:has-text("Accept all")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=1500)
                page.wait_for_timeout(400)
                return
        except Exception:  # noqa: BLE001
            continue


def _parse_google_html_results(html: str, max_results: int) -> list[dict]:
    """Parse classic/HTML Google result anchors when JS SERP is blocked."""
    results: list[dict] = []
    seen: set[str] = set()
    # Match <a href="/url?q=...">title</a> and plain result links.
    pattern = re.compile(
        r'<a[^>]+href="(/url\?q=[^"]+|https?://(?!www\.google\.)[^"]+)"[^>]*>'
        r"(?:<div[^>]*>)?([^<]{3,180})",
        re.I,
    )
    for match in pattern.finditer(html):
        href = unescape(match.group(1))
        title = unescape(re.sub(r"\s+", " ", match.group(2))).strip()
        if href.startswith("/url?q="):
            qs = parse_qs(urlparse("https://www.google.com" + href).query)
            href = (qs.get("q") or qs.get("url") or [""])[0]
        if not href.startswith("http"):
            continue
        host = (urlparse(href).hostname or "").lower()
        if "google." in host:
            continue
        if href in seen or len(title) < 3:
            continue
        if title.lower() in {"cached", "similar", "translate this page"}:
            continue
        seen.add(href)
        results.append({"title": title, "url": href, "snippet": ""})
        if len(results) >= max_results:
            break
    return results


_BING_EXTRACT_JS = """(maxResults) => {
  const results = [];
  const seen = new Set();
  for (const item of document.querySelectorAll("#b_results > li.b_algo")) {
    const a = item.querySelector("h2 a");
    if (!a) continue;
    const href = a.href || "";
    const title = (a.innerText || "").trim();
    if (!href.startsWith("http") || !title || seen.has(href)) continue;
    const snip = (item.querySelector(".b_caption p, p")?.innerText || "")
      .replace(/\\s+/g, " ")
      .trim();
    seen.add(href);
    results.push({ title, url: href, snippet: snip.slice(0, 320) });
    if (results.length >= maxResults) break;
  }
  return results;
}"""

_BRAVE_EXTRACT_JS = """(maxResults) => {
  const results = [];
  const seen = new Set();
  const anchors = document.querySelectorAll(
    'div[data-type="web"] a[href^="http"], main a[href^="http"]'
  );
  for (const a of anchors) {
    const href = a.href || "";
    const title = (a.innerText || "").trim();
    if (!href.startsWith("http") || title.length < 8 || seen.has(href)) continue;
    if (/brave\\.com\\//i.test(href)) continue;
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
  return results;
}"""


def _unwrap_result_url(href: str) -> str:
    """Decode Bing/Google redirect wrappers to the destination URL."""
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        host = (parsed.hostname or "").lower()
        if "bing.com" in host and "u" in qs:
            token = qs["u"][0]
            if token.startswith("a1"):
                raw = token[2:]
                raw += "=" * (-len(raw) % 4)
                decoded = base64.urlsafe_b64decode(raw.encode("utf-8")).decode(
                    "utf-8", errors="ignore"
                )
                if decoded.startswith("http"):
                    return decoded
        if "google." in host and parsed.pathname == "/url":
            dest = (qs.get("q") or qs.get("url") or [""])[0]
            if dest.startswith("http"):
                return unquote(dest)
    except Exception:  # noqa: BLE001
        return href
    return href


def _engine_search_sync(
    *,
    engine: str,
    url: str,
    extract_js: str,
    query: str,
    timeout_ms: int,
    max_results: int,
) -> dict:
    browser = _ensure_browser()
    context = _new_context(browser)
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(8_000, timeout_ms))
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(700)
        results = page.evaluate(extract_js, max_results) or []
        cleaned = []
        for item in results[:max_results]:
            cleaned.append(
                {
                    **item,
                    "url": _unwrap_result_url(item.get("url") or ""),
                }
            )
        return {
            "status": response.status if response is not None else 0,
            "final_url": page.url,
            "blocked": False,
            "source": engine,
            "query": query,
            "results": cleaned,
        }
    finally:
        context.close()


def _google_search_sync(query: str, *, timeout_ms: int, max_results: int) -> dict:
    browser, engine = _ensure_search_browser()
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
        if engine == "firefox"
        else (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    )
    context = browser.new_context(
        user_agent=ua,
        viewport={"width": 1365, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
        java_script_enabled=True,
        ignore_https_errors=False,
    )
    if engine != "firefox":
        context.add_init_script(_STEALTH_INIT)
    page = context.new_page()
    try:
        # 1) Land on Google like a normal user, accept consent, type the query.
        response = page.goto(
            "https://www.google.com/?hl=en",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        _dismiss_google_consent(page)
        page.wait_for_timeout(500)
        _dismiss_google_consent(page)

        typed = False
        for sel in ['textarea[name="q"]', 'input[name="q"]']:
            try:
                box = page.locator(sel).first
                if box.count() == 0:
                    continue
                box.click(timeout=2000)
                box.fill("")
                page.keyboard.type(query, delay=40)
                page.keyboard.press("Enter")
                typed = True
                break
            except Exception:  # noqa: BLE001
                continue

        if not typed:
            serp = (
                "https://www.google.com/search?"
                f"q={quote_plus(query)}&hl=en&gl=us&num={max_results}&pws=0"
            )
            response = page.goto(serp, wait_until="domcontentloaded", timeout=timeout_ms)

        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(12_000, timeout_ms))
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(900)
        _dismiss_google_consent(page)

        data = page.evaluate(_GOOGLE_EXTRACT_JS) or {}
        status = response.status if response is not None else 0
        blocked = bool(data.get("blocked")) or "/sorry/" in (page.url or "")
        results = (data.get("results") or [])[:max_results]

        # 2) HTML-only Google results if JS SERP is empty/challenged.
        if blocked or not results:
            html_url = (
                "https://www.google.com/search?"
                f"q={quote_plus(query)}&hl=en&gl=us&num={max_results}&pws=0&gbv=1"
            )
            response = page.goto(
                html_url, wait_until="domcontentloaded", timeout=timeout_ms
            )
            _dismiss_google_consent(page)
            page.wait_for_timeout(600)
            html = page.content()
            html_blocked = "/sorry/" in page.url or "unusual traffic" in html.lower()
            if not html_blocked:
                results = _parse_google_html_results(html, max_results)
                blocked = False
                status = response.status if response is not None else status

        if results:
            return {
                "status": status,
                "final_url": page.url,
                "blocked": False,
                "source": f"google/{engine}",
                "results": results[:max_results],
            }
        return {
            "status": status,
            "final_url": page.url,
            "blocked": True,
            "source": f"google/{engine}",
            "results": [],
        }
    finally:
        context.close()


def google_search_results(
    query: str,
    *,
    max_results: int = 8,
    timeout_ms: int = 35_000,
) -> dict:
    """Browser search with no API key: Google first, then Bing/Brave fallbacks."""
    q = " ".join((query or "").split())
    if not q:
        raise ValueError("query is empty")
    max_results = max(1, min(int(max_results), 10))

    def _run() -> dict:
        try:
            data = _google_search_sync(
                q, timeout_ms=timeout_ms, max_results=max_results
            )
        except Exception:
            logger.exception("google path failed for %r", q)
            data = {
                "blocked": True,
                "results": [],
                "status": 0,
                "final_url": "",
                "source": "google",
            }
        if data.get("results"):
            return data

        # Cloud IPs often get Google CAPTCHA — continue with other SERPs, still
        # via a real browser and still without a Search API key.
        fallbacks = [
            (
                "bing",
                f"https://www.bing.com/search?q={quote_plus(q)}&setlang=en-US",
                _BING_EXTRACT_JS,
            ),
            (
                "brave",
                f"https://search.brave.com/search?q={quote_plus(q)}",
                _BRAVE_EXTRACT_JS,
            ),
        ]
        for engine, url, extract_js in fallbacks:
            try:
                fallback = _engine_search_sync(
                    engine=engine,
                    url=url,
                    extract_js=extract_js,
                    query=q,
                    timeout_ms=timeout_ms,
                    max_results=max_results,
                )
            except Exception:
                logger.exception("%s search fallback failed for %r", engine, q)
                continue
            if fallback.get("results"):
                fallback["note"] = (
                    "Google served a bot check from this host; "
                    f"returned {engine} browser results instead (still no API key)."
                )
                return fallback
        return data

    future = _POOL.submit(_run)
    try:
        return future.result(timeout=(timeout_ms / 1000.0) + 45)
    except Exception:
        logger.exception("google_search failed for %r", q)
        _POOL.submit(_close_browser).result(timeout=10)
        raise
