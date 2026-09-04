# AGENTS.md — Instructions for AI coding assistants

This file tells AI coding assistants (Claude Code, Cursor, Copilot, Codex, etc.)
how to extend this project.

## Project overview

A DuckDuckGo Search MCP server that exposes four search tools via the Model
Context Protocol (MCP) over Streamable HTTP transport, wrapped in FastAPI for
auth and health checks.  Deployed on Render.

- **Server**: `server.py` — FastAPI app with FastMCP mounted at `/mcp`
- **Tests**: `tests/test_server.py` — pytest, using `unittest.mock`
- **Deps**: `requirements.txt` — fastmcp, ddgs, trafilatura, uvicorn

## How to add a new tool

1. Open `server.py`.
2. Add a new function decorated with `@mcp.tool()`.
3. The docstring becomes the tool's description (shown to LLMs).  Always write
   a clear one-liner plus a detailed paragraph explaining parameters.
4. Type annotations define the JSON Schema input contract — use `str`, `int`,
   `Optional[str]`, etc.
5. Clamp `max_results` or similar bounds inside the function.
6. For search tools, call `_run_search(method, backend=..., **filters)` — it
   reads the default backend from `DDGS_BACKEND`, retries transient failures,
   and converts engine errors (403/ratelimit IP blocks, timeouts) into a
   readable one-element error list instead of raising.  Do NOT call
   `_make_ddgs()` directly from a tool.
7. Sanitise results with `_safe_result()` (done inside `_run_search`).
8. Add tests in `tests/test_server.py` — mock `server._make_ddgs`.
9. Run tests: `pytest`

## Conventions

- Camouflage private helpers with leading underscore: `_make_ddgs`, `_safe_result`,
  `_run_search`, `_classify_error`.
- Clamp user-facing numeric params instead of raising errors on out-of-range.
- Every tool returns `list[dict]` — never raise from inside a tool; on failure
  return `[{"error": ..., "reason": ..., "hint": ...}]` so the MCP client gets
  a readable explanation (hosts on Render/Vercel often have blocked IPs —
  surface that, don't hide it).
- Descriptions are LLM-facing — be specific about supported operators
  (e.g. `filetype:`, `site:`), valid enum values, and defaults.
