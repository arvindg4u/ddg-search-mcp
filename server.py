"""
DuckDuckGo Search MCP Server
=============================
Search the web via DuckDuckGo — text, news, images, and videos — through the
Model Context Protocol (MCP).  Runs on Streamable HTTP transport.

Designed for Render deployment via the included render.yaml Blueprint.
Built with:
  - fastmcp 3.x  — MCP server framework
  - ddgs 8.x     — DuckDuckGo search library (no API key required)

Usage
-----
  python server.py          # start server on PORT (default 10000)

Env vars
--------
  PORT            int   Server port                (default 10000)
  MCP_API_TOKEN   str   Bearer token for auth      (default: no auth)
  DDGS_TIMEOUT    int   Search request timeout (s)  (default 10)
  DDGS_PROXY      str   Proxy URL                  (default: none)
"""

from __future__ import annotations

import hmac
import os
from typing import Any

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
    version="1.0.0",
    instructions="""\
This server provides DuckDuckGo search capabilities through four tools:

- **ddg_search**:  General web search (supports filetype:, site: operators)
- **ddg_news**:    Recent news article search
- **ddg_images**:  Image search (size, colour, type filters)
- **ddg_videos**:  Video search (resolution, duration filters)

Each tool accepts a `region` parameter (e.g. us-en, uk-en, wt-wt for worldwide),
a `safesearch` filter (on / moderate / off), and time-limit filters.""",
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


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ddg_search",
    description=(
        "Search the web via DuckDuckGo.  Returns title, URL, and body snippet. "
        "Supports operators: filetype:pdf, site:example.com, intitle:, inurl:."
    ),
)
def ddg_search(
    query: str,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
    max_results: int = 10,
) -> list[dict[str, str]]:
    """General web search.

    Args:
        query:       Search keywords (supports filetype:, site:, intitle:, inurl:).
        region:      Region code — us-en, uk-en, wt-wt (worldwide), etc.
        safesearch:  "on", "moderate", or "off".
        timelimit:   "d", "w", "m", "y" or None for all time.
        max_results: Number of results (1–50, default 10).
    """
    ddgs = _make_ddgs()
    results = ddgs.text(
        query=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=_clamp(max_results, 1, 50),
    )
    return [_safe_result(r) for r in results]


@mcp.tool(
    name="ddg_news",
    description=(
        "Search recent news via DuckDuckGo.  Returns title, body, URL, "
        "source name, and publication date."
    ),
)
def ddg_news(
    query: str,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
    max_results: int = 10,
) -> list[dict[str, str]]:
    """News article search.

    Args:
        query:       Search keywords.
        region:      Region code.
        safesearch:  "on", "moderate", or "off".
        timelimit:   "d", "w", "m" or None for all time.
        max_results: Number of results (1–50, default 10).
    """
    ddgs = _make_ddgs()
    results = ddgs.news(
        query=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=_clamp(max_results, 1, 50),
    )
    return [_safe_result(r) for r in results]


@mcp.tool(
    name="ddg_images",
    description=(
        "Search images via DuckDuckGo.  Returns title, image URL, thumbnail, "
        "source page, dimensions, and source name.  Supports size, colour, "
        "and type filters."
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
    """
    ddgs = _make_ddgs()
    results = ddgs.images(
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
    return [_safe_result(r) for r in results]


@mcp.tool(
    name="ddg_videos",
    description=(
        "Search videos via DuckDuckGo (aggregates YouTube, Bing Videos, etc.). "
        "Returns title, URL, duration, uploader, publish date, and provider."
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
    """
    ddgs = _make_ddgs()
    results = ddgs.videos(
        query=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        resolution=resolution,
        duration=duration,
        max_results=_clamp(max_results, 1, 50),
    )
    return [_safe_result(r) for r in results]


# ---------------------------------------------------------------------------
# Health endpoint (bypasses auth — custom_route makes it public)
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Any) -> JSONResponse:
    """Render health-check endpoint."""
    return JSONResponse({"status": "ok", "service": "ddg-search-mcp"})


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
