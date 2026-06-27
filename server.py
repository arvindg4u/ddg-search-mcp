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

import os
import re
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(os.getenv("PORT", "10000"))
MCP_API_TOKEN = os.getenv("MCP_API_TOKEN", "")
DDGS_TIMEOUT = int(os.getenv("DDGS_TIMEOUT", "10"))
DDGS_PROXY = os.getenv("DDGS_PROXY", "") or None

AUTH_ENABLED = bool(MCP_API_TOKEN)

# ---------------------------------------------------------------------------
# MCP Server — single responsibility: web search via DuckDuckGo
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
        keywords=query,
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
        keywords=query,
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
        keywords=query,
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
        keywords=query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        resolution=resolution,
        duration=duration,
        max_results=_clamp(max_results, 1, 50),
    )
    return [_safe_result(r) for r in results]


# ---------------------------------------------------------------------------
# Starlette application  (wraps MCP with auth + health-check)
# ---------------------------------------------------------------------------


async def _health(request: Request) -> JSONResponse:
    """Render health-check endpoint."""
    return JSONResponse({"status": "ok", "service": "ddg-search-mcp"})


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated requests when MCP_API_TOKEN is set."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not AUTH_ENABLED:
            return await call_next(request)

        # Allow health checks without auth.
        if request.url.path == "/health":
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        expected = f"Bearer {MCP_API_TOKEN}"

        if auth_header != expected:
            return Response(
                status_code=401,
                content='{"error":"Unauthorized"}',
                media_type="application/json",
            )

        return await call_next(request)


# Build the ASGI app.
app = Starlette(
    routes=[
        Route("/health", endpoint=_health, methods=["GET"]),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        Middleware(AuthMiddleware),
    ],
)

# Mount the MCP server's internal ASGI app at /mcp.
app.mount("/mcp", mcp.http_app())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Starting DDG Search MCP server on 0.0.0.0:{PORT}")
    print(f"  MCP endpoint:  http://0.0.0.0:{PORT}/mcp")
    print(f"  Health check:  http://0.0.0.0:{PORT}/health")
    print(f"  Auth enabled:  {AUTH_ENABLED}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
