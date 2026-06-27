# DuckDuckGo Search MCP Server

An [MCP](https://modelcontextprotocol.io/) server that brings DuckDuckGo search to any MCP-compatible AI client (Claude Desktop, Cursor, VS Code, Copilot, etc.).

**No API key required** — DuckDuckGo is free and public.

## Tools

| Tool | Description |
|------|-------------|
| `ddg_search` | General web search (supports `filetype:`, `site:`, `intitle:`, `inurl:` operators) |
| `ddg_news` | Recent news article search |
| `ddg_images` | Image search with size, colour, and type filters |
| `ddg_videos` | Video search (aggregates YouTube, Bing Videos, etc.) |
| `ddg_fetch` | **NEW** — Fetch a URL and extract readable content (article body, headings, metadata). Uses [trafilatura](https://trafilatura.readthedocs.io/) to strip navigation, ads, and boilerplate. |

Every search tool supports `region` (e.g. `us-en`, `uk-en`, `wt-wt`), `safesearch` (`on` / `moderate` / `off`), and time-limit filters.

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Run (no auth — local dev only)
python server.py
```

The server starts at `http://localhost:10000`.  
MCP endpoint: `http://localhost:10000/mcp`  
Health check: `http://localhost:10000/health`

## Connect from AI Clients

### Claude Desktop
Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ddg-search": {
      "type": "streamable-http",
      "url": "https://ddg-search-mcp.onrender.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

### Cursor
Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ddg-search": {
      "url": "https://ddg-search-mcp.onrender.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

### VS Code / Copilot
Add to your MCP configuration:

```json
{
  "inputs": [
    {
      "type": "mcp",
      "name": "ddg-search",
      "url": "https://ddg-search-mcp.onrender.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  ]
}
```

## Deploy to Render

### One-click with Blueprint

1. Push this repo to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com), click **New → Blueprint**.
3. Connect your repo — the included `render.yaml` auto-configures everything.

Render auto-generates an `MCP_API_TOKEN`. Find it under **Dashboard → your service → Environment**.

### Manual deploy

1. Push to GitHub.
2. Render Dashboard → **New → Web Service**.
3. Connect repo, set:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `python server.py`
   - **Health check:** `/health`
4. Deploy.

After deploy, find your auto-generated token in **Environment** or generate one:

```bash
openssl rand -base64 32
```

### Free plan & keep-alive

Render's free services spin down after **15 minutes of inactivity**, causing 30–60s cold starts. This repo includes a **GitHub Actions keep-alive cron** (`.github/workflows/keep-alive.yml`) that pings the health endpoint every 10 minutes to prevent spin-down.

To enable it:
1. Go to your repo on GitHub → **Actions** tab.
2. Enable GitHub Actions (if prompted).
3. The `Keep Render Alive` workflow runs automatically on the `*/10 * * * *` schedule.

> **Note:** GitHub Actions has a [usage limit](https://docs.github.com/en/billing/managing-billing-for-github-actions/about-billing-for-github-actions) on free plans (2,000 minutes/month). This workflow uses ~4,500 minutes/year (~375 min/month) — well within the free tier. For guaranteed zero latency, upgrade to Render's Starter plan ($7/mo).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `10000` | Server port |
| `MCP_API_TOKEN` | *(none)* | Bearer token for authentication (unset = no auth) |
| `DDGS_TIMEOUT` | `10` | DuckDuckGo request timeout in seconds |
| `DDGS_PROXY` | *(none)* | Proxy URL (http/https/socks5) or `"tb"` for Tor |

## Architecture

```
┌──────────────────────────────────────────────┐
│  Starlette ASGI app                          │
│                                              │
│  ┌──────────┐  ┌──────────────────────────┐  │
│  │ /health  │  │ /mcp                     │  │
│  │ (no auth)│  │ (FastMCP Streamable HTTP)│  │
│  └──────────┘  │                          │  │
│                 │  ddg_search()            │  │
│  Middleware:    │  ddg_news()              │  │
│  • CORS        │  ddg_images()            │  │
│  • Auth (JWT)  │  ddg_videos()            │  │
│                 └──────────────────────────┘  │
└──────────────────────────────────────────────┘
```

## Development

```bash
pip install -r requirements.txt
python server.py
```

### Testing

```bash
pytest tests/ -v
```

### Adding a new tool

See `AGENTS.md` for full conventions — the short version:

1. Add a decorated function in `server.py`:
   ```python
   @mcp.tool()
   def ddg_my_tool(query: str, max_results: int = 10) -> list[dict]:
       """Description for LLMs."""
       ddgs = _make_ddgs()
       results = ddgs.some_method(query=query, max_results=_clamp(max_results, 1, 50))
       return [_safe_result(r) for r in results]
   ```
   For web-fetch tools, use `httpx` + `trafilatura` (see `ddg_fetch` for the pattern).
2. Add tests in `tests/test_server.py`.

## License

MIT
