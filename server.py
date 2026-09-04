"""
DuckDuckGo Search MCP Server
=============================
Search the web via DuckDuckGo — text, news, images, and videos — through the
Model Context Protocol (MCP).  Runs on Streamable HTTP transport.

Designed for Render deployment via the included render.yaml Blueprint.
Built with:
  - fastmcp 3.x  — MCP server framework
  - ddgs 9.x     — DuckDuckGo search library (no API key required)
  - trafilatura  — Web page content extraction for ddg_fetch

Usage
-----
  python server.py          # start server on PORT (default 10000)

Env vars
--------
  PORT            int   Server port                (default 10000)
  MCP_API_TOKEN   str   Bearer token for auth      (default: no auth)
  DDGS_TIMEOUT    int   Search request timeout (s)  (default 10)
  DDGS_PROXY      str   Proxy URL                  (default: none)
  DDGS_BACKEND    str   Default search backend      (default: auto)

Datacenter IP blocks
--------------------
Search engines (DuckDuckGo in particular) frequently reject requests from
datacenter IPs — Render, Vercel, Heroku, AWS, etc. — with 403 / 202
ratelimit responses.  The server therefore:

  * falls back across engines automatically (ddgs "auto" backend), and
    lets each tool call force a specific engine via its `backend` param;
  * returns a structured error entry with a human-readable hint instead
    of raising, so MCP clients can explain what went wrong;
  * exposes a /status endpoint that probes every engine from the host
    and reports which ones are reachable (use it to diagnose blocks).
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import time
from typing import Any

import httpx
import trafilatura
import uvicorn
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(os.getenv("PORT", "10000"))
MCP_API_TOKEN = os.getenv("MCP_API_TOKEN", "")
DDGS_TIMEOUT = int(os.getenv("DDGS_TIMEOUT", "10"))
DDGS_PROXY = os.getenv("DDGS_PROXY", "") or None
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

AUTH_ENABLED = bool(MCP_API_TOKEN)

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="ddg-search",
    version="1.1.0",
    instructions="""\
This server provides web search capabilities and page fetching:

- **ddg_search**:  General web search (supports filetype:, site: operators)
- **ddg_news**:    Recent news article search
- **ddg_images**:  Image search (size, colour, type filters)
- **ddg_videos**:  Video search (resolution, duration filters)
- **ddg_fetch**:   Fetch and extract readable content from a URL

Searches fall back across multiple engines automatically. Each search tool
accepts a `backend` parameter to force a specific engine (e.g. "bing") when
the default one is blocked, a `region` parameter (e.g. us-en, uk-en, wt-wt
for worldwide), a `safesearch` filter (on / moderate / off), and time-limit
filters.

If a search fails, the tool returns a single-entry list with "error",
"reason", and "hint" keys — explain the hint to the user or retry with a
different backend.""",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ddgs():
    """Return a configured DDGS instance."""
    from ddgs import DDGS

    kwargs: dict[str, Any] = {"timeout": DDGS_TIMEOUT}
    if DDGS_PROXY:
        kwargs["proxy"] = DDGS_PROXY
    return DDGS(**kwargs)


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    """Strip any internal / unexpected keys from a search result dict."""
    skip = {"_raw", "raw", "source_internal"}
    return {k: v for k, v in result.items() if k not in skip}


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp *value* to the inclusive range [*lo*, *hi*]."""
    return max(lo, min(value, hi))


def _default_backend() -> str:
    """Backend to use when a tool call doesn't specify one.

    Reads DDGS_BACKEND at call time so it can be changed without a redeploy
    of the module (and so tests can vary it).  Empty/unset means "auto".
    """
    return (os.getenv("DDGS_BACKEND", "") or "auto").strip().lower() or "auto"


_BLOCKED_HINT = (
    "The search engine rejected this server's IP (403/202 ratelimit) — this is "
    "common on hosting platforms (Render, Vercel, Heroku, AWS) whose datacenter "
    "IPs are blocked. Fixes: (1) retry with a different backend (e.g. "
    "backend='auto' or 'bing'); (2) set DDGS_PROXY on the server to a "
    "residential/rotating proxy; (3) check GET /status to see which engines "
    "work from this host; (4) run the server locally instead."
)
_TIMEOUT_HINT = (
    "The search timed out. Retry, request fewer results, or increase the "
    "DDGS_TIMEOUT env var (default 10s)."
)
_NETWORK_HINT = (
    "Could not reach the search engine (connection/TLS error). The host's "
    "network may block outbound traffic, or the engine may be blocking this "
    "IP. Set DDGS_PROXY or check GET /status."
)
_NO_RESULTS_HINT = (
    "No results were found. If that seems wrong, the engine may be silently "
    "dropping requests from this server's IP — check GET /status."
)


def _classify_error(exc: BaseException) -> tuple[str, str]:
    """Map an exception raised by ddgs to a (reason, hint) pair."""
    msg = f"{exc}".lower()
    name = type(exc).__name__.lower()
    if (
        "403" in msg
        or "forbidden" in msg
        or "ratelimit" in msg
        or "rate limit" in msg
        or "challenge" in msg
        or "captcha" in msg
        or "ratelimit" in name
    ):
        return "ip_blocked", _BLOCKED_HINT
    if "timed out" in msg or "timeout" in msg or "timeout" in name:
        return "timeout", _TIMEOUT_HINT
    if (
        "connect" in msg
        or "tls" in msg
        or "ssl" in msg
        or "name or service not known" in msg
        or "network" in msg
    ):
        return "network", _NETWORK_HINT
    if "no results found" in msg:
        return "no_results", _NO_RESULTS_HINT
    return "unknown", (
        f"Search failed unexpectedly ({type(exc).__name__}). "
        "Retry, try another backend, or check GET /status."
    )


def _run_search(
    method: str,
    *,
    backend: str | None = None,
    retry: int = 1,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Run a ddgs search with graceful error handling.

    Never raises.  Returns the list of results on success, or a one-element
    list containing an error descriptor (error / reason / hint / backend)
    so MCP clients get a readable explanation instead of a stack trace.

    Args:
        method:  ddgs method name — "text", "news", "images", or "videos".
        backend: Engine key, comma-separated keys, or "auto".  None means
                 use the DDGS_BACKEND env var (default "auto").
        retry:   Extra attempts for transient (timeout/network) failures.
        **kwargs: Passed straight to the ddgs method.
    """
    ddgs = _make_ddgs()
    backend = backend or _default_backend()
    last_exc: BaseException | None = None

    for attempt in range(retry + 1):
        try:
            results = getattr(ddgs, method)(backend=backend, **kwargs)
            return [_safe_result(r) for r in results]
        except Exception as e:  # noqa: BLE001 — surface, don't crash the tool
            last_exc = e
            reason, _ = _classify_error(e)
            if reason in ("timeout", "network") and attempt < retry:
                time.sleep(0.5 * (attempt + 1))
                continue
            break

    assert last_exc is not None  # only reachable when an exception occurred
    reason, hint = _classify_error(last_exc)
    return [
        {
            "error": f"Search failed ({reason}): "
            f"{type(last_exc).__name__}: {last_exc}"[:400],
            "reason": reason,
            "hint": hint,
            "backend": backend,
        }
    ]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ddg_search",
    description=(
        "Search the web.  Returns title, URL, and body snippet.  Supports "
        "operators: filetype:pdf, site:example.com, intitle:, inurl:.  Falls "
        "back across engines automatically; pass backend to force one."
    ),
)
def ddg_search(
    query: str,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
    max_results: int = 10,
    backend: str | None = None,
) -> list[dict[str, Any]]:
    """General web search.

    Args:
        query:       Search keywords (supports filetype:, site:, intitle:, inurl:).
        region:      Region code — us-en, uk-en, wt-wt (worldwide), etc.
        safesearch:  "on", "moderate", or "off".
        timelimit:   "d", "w", "m", "y" or None for all time.
        max_results: Number of results (1–50, default 10).
        backend:     Engine(s) to use: "auto" (default; tries all), or one of
                     duckduckgo, brave, google, mojeek, startpage, wikipedia,
                     yahoo, grokipedia.  Try another engine if one is blocked.
    """
    return _run_search(
        "text",
        backend=backend,
        query=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=_clamp(max_results, 1, 50),
    )


@mcp.tool(
    name="ddg_news",
    description=(
        "Search recent news.  Returns title, body, URL, source name, and "
        "publication date.  Falls back across engines automatically; pass "
        "backend to force one."
    ),
)
def ddg_news(
    query: str,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
    max_results: int = 10,
    backend: str | None = None,
) -> list[dict[str, Any]]:
    """News article search.

    Args:
        query:       Search keywords.
        region:      Region code.
        safesearch:  "on", "moderate", or "off".
        timelimit:   "d", "w", "m" or None for all time.
        max_results: Number of results (1–50, default 10).
        backend:     Engine(s) to use: "auto" (default; tries all), or one of
                     duckduckgo, bing, yahoo.  Try another engine if one is
                     blocked.
    """
    return _run_search(
        "news",
        backend=backend,
        query=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=_clamp(max_results, 1, 50),
    )


@mcp.tool(
    name="ddg_images",
    description=(
        "Search images.  Returns title, image URL, thumbnail, source page, "
        "dimensions, and source name.  Supports size, colour, and type "
        "filters.  Falls back across engines automatically; pass backend to "
        "force one."
    ),
)
def ddg_images(
    query: str,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
    size: str | None = None,
    color: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
    max_results: int = 10,
    backend: str | None = None,
) -> list[dict[str, Any]]:
    """Image search.

    Args:
        query:       Search keywords.
        region:      Region code.
        safesearch:  "on", "moderate", or "off".
        timelimit:   "Day", "Week", "Month", "Year", or None.
        size:        "Small", "Medium", "Large", "Wallpaper", or None.
        color:       "Monochrome", "Red", "Green", "Blue", … or None.
        type_image:  "photo", "clipart", "gif", "transparent", "line", or None.
        layout:      "Square", "Tall", "Wide", or None.
        max_results: Number of results (1–100, default 10).
        backend:     Engine(s) to use: "auto" (default; tries all), or one of
                     duckduckgo, bing.  Try another engine if one is blocked.
    """
    return _run_search(
        "images",
        backend=backend,
        query=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        size=size,
        color=color,
        type_image=type_image,
        layout=layout,
        max_results=_clamp(max_results, 1, 100),
    )


@mcp.tool(
    name="ddg_videos",
    description=(
        "Search videos (aggregates YouTube, Bing Videos, etc.).  Returns "
        "title, URL, duration, uploader, publish date, and provider."
    ),
)
def ddg_videos(
    query: str,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
    resolution: str | None = None,
    duration: str | None = None,
    max_results: int = 10,
    backend: str | None = None,
) -> list[dict[str, Any]]:
    """Video search.

    Args:
        query:       Search keywords.
        region:      Region code.
        safesearch:  "on", "moderate", or "off".
        timelimit:   "d", "w", "m" or None.
        resolution:  "high" or "standart" (sic) — or None.
        duration:    "short", "medium", "long" — or None.
        max_results: Number of results (1–50, default 10).
        backend:     Engine(s) to use: "auto" (default), or "duckduckgo"
                     (the only video engine available).
    """
    return _run_search(
        "videos",
        backend=backend,
        query=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        resolution=resolution,
        duration=duration,
        max_results=_clamp(max_results, 1, 50),
    )


# ---------------------------------------------------------------------------
# Web fetch tool
# ---------------------------------------------------------------------------

FETCH_TIMEOUT = int(os.getenv("DDGS_TIMEOUT", "30"))
FETCH_USER_AGENT = (
    "Mozilla/5.0 (compatible; DDGSearchBot/1.0; "
    "https://ddg-search-mcp.onrender.com)"
)
MAX_FETCH_SIZE = 5_000_000  # 5 MB


@mcp.tool(
    name="ddg_fetch",
    description=(
        "Fetch a URL and extract its main readable content (article body, "
        "headings, and metadata).  Returns the page title, extracted text, "
        "and source URL.  Useful for reading full articles after a search."
    ),
)
def ddg_fetch(
    url: str,
    max_chars: int = 10_000,
) -> dict[str, str]:
    """Fetch and extract readable content from a URL.

    Uses trafilatura to extract the main article content, stripping
    navigation, ads, and boilerplate.

    Args:
        url:        The full URL (http:// or https://) to fetch.
        max_chars:  Maximum characters of extracted text to return (500–50_000).
                   Defaults to 10_000.

    Returns:
        Dict with keys: url, title, text, content_type.
        If extraction fails, text contains the raw HTML title or error message.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    max_chars = _clamp(max_chars, 500, 50_000)

    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": FETCH_USER_AGENT},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()

            # Guard against huge responses
            content = resp.content
            if len(content) > MAX_FETCH_SIZE:
                content = content[:MAX_FETCH_SIZE]
            html = resp.text

        # Extract readable content
        extracted = trafilatura.extract(
            html,
            output_format="txt",
            include_links=True,
            include_tables=True,
        )

        # Title comes from page metadata (NOT a second full-text extraction)
        title: str | None = None
        try:
            meta = trafilatura.extract_metadata(html)
            meta_title = getattr(meta, "title", None)
            if isinstance(meta_title, str) and meta_title.strip():
                title = meta_title.strip()
        except Exception:  # noqa: BLE001 — fall back to <title> regex below
            title = None

        if not title:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = m.group(1).strip() if m else url

        if extracted:
            text = extracted[:max_chars]
        else:
            # Fallback: return first meaningful text block
            # Strip tags, get body text
            body = re.sub(r"<[^>]+>", " ", html)
            body = re.sub(r"\s+", " ", body).strip()
            text = body[:max_chars] if body else f"Could not extract content from {url}"

        return {
            "url": str(resp.url),
            "title": title if isinstance(title, str) else url,
            "text": text,
            "content_type": resp.headers.get("content-type", "unknown"),
        }

    except httpx.HTTPStatusError as e:
        return {
            "url": url,
            "title": "HTTP Error",
            "text": f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
            "content_type": "error",
        }
    except httpx.TimeoutException:
        return {
            "url": url,
            "title": "Timeout",
            "text": f"Request timed out after {FETCH_TIMEOUT}s",
            "content_type": "error",
        }
    except Exception as e:
        return {
            "url": url,
            "title": "Error",
            "text": f"Failed to fetch URL: {type(e).__name__}: {e}",
            "content_type": "error",
        }


# ---------------------------------------------------------------------------
# Health endpoint (bypasses auth — custom_route makes it public)
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Any) -> JSONResponse:
    """Render health-check endpoint."""
    return JSONResponse({"status": "ok", "service": "ddg-search-mcp"})


# ---------------------------------------------------------------------------
# Status / diagnostics endpoint (auth-protected — only /health is public)
# ---------------------------------------------------------------------------

PROBE_CATEGORIES = ("text", "news", "images", "videos")


def _probe_backends(categories: list[str]) -> dict[str, dict[str, Any]]:
    """Run a tiny 1-result probe against every engine in each category.

    Used by GET /status to show which search engines are reachable from
    this host — the quickest way to diagnose datacenter IP blocks on
    Render / Vercel / Heroku.
    """
    from ddgs.engines import ENGINES

    ddgs = _make_ddgs()
    report: dict[str, dict[str, Any]] = {}
    for category in categories:
        engines = sorted(ENGINES.get(category, {}).keys())
        category_report: dict[str, Any] = {}
        for engine_key in engines:
            try:
                results = getattr(ddgs, category)(
                    query="test", backend=engine_key, max_results=1
                )
                category_report[engine_key] = {"ok": True, "results": len(results)}
            except Exception as e:  # noqa: BLE001 — report, don't crash
                reason, _ = _classify_error(e)
                category_report[engine_key] = {
                    "ok": False,
                    "reason": reason,
                    "error": f"{type(e).__name__}: {e}"[:200],
                }
        report[category] = category_report
    return report


@mcp.custom_route("/status", methods=["GET"])
async def status(request: Any) -> JSONResponse:
    """Diagnostics: probe each search engine and report what works.

    Query params:
        category:  optional — one of text, news, images, videos to probe
                   only that category (otherwise all are probed).

    Each probe is a 1-result search, so this makes real outbound requests
    and can take up to ~2 min if engines hang until timeout.  Requires the
    bearer token (same as /mcp) when auth is enabled.
    """
    category = request.query_params.get("category", "").strip().lower()
    categories = [category] if category in PROBE_CATEGORIES else list(PROBE_CATEGORIES)

    backends = await asyncio.to_thread(_probe_backends, categories)
    return JSONResponse(
        {
            "service": "ddg-search-mcp",
            "proxy_configured": bool(DDGS_PROXY),
            "timeout_s": DDGS_TIMEOUT,
            "backends": backends,
            "note": (
                "Each engine was probed with a 1-result query from this host. "
                "ok=false with reason=ip_blocked means the engine rejected "
                "this server's IP (common on Render/Vercel/Heroku) — use a "
                "backend that reports ok=true, or set DDGS_PROXY."
            ),
        }
    )


# ---------------------------------------------------------------------------
# Bearer auth middleware (ASGI-level, same pattern as Render template)
# ---------------------------------------------------------------------------


class BearerAuthMiddleware:
    """Reject unauthenticated requests when MCP_API_TOKEN is set."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Only protect HTTP paths; skip health (it has its own route).
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        expected = f"Bearer {MCP_API_TOKEN}"

        # Constant-time comparison to prevent timing side-channel attacks.
        if hmac.compare_digest(auth, expected):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": "Unauthorized"},
                "id": None,
            },
            status_code=401,
        )
        await response(scope, receive, send)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app():
    """Build and return the ASGI app.

    Using a factory means env vars are read at call time, not import time,
    which makes testing easier.
    """
    raw = mcp.http_app(path="/mcp", transport="streamable-http", stateless_http=True)

    if AUTH_ENABLED:
        # Wrap the raw app with auth using pure ASGI middleware,
        # preserving lifespan for the Streamable HTTP session manager.
        wrapped = BearerAuthMiddleware(raw)
        wrapped.lifespan = raw.lifespan
        return wrapped

    return raw


app = create_app()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not MCP_API_TOKEN:
        print("WARNING: MCP_API_TOKEN is not set. Server running WITHOUT auth.")

    print(f"Starting DDG Search MCP server on 0.0.0.0:{PORT}")
    print(f"  MCP endpoint:  http://0.0.0.0:{PORT}/mcp")
    print(f"  Health check:  http://0.0.0.0:{PORT}/health")
    print(f"  Auth enabled:  {AUTH_ENABLED}")
    uvicorn.run(create_app(), host="0.0.0.0", port=PORT)
