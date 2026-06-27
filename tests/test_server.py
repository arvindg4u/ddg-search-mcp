"""Tests for the DuckDuckGo Search MCP server."""

import os
from unittest.mock import patch, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport
from contextlib import asynccontextmanager

from server import ddg_search, ddg_news, ddg_images, ddg_videos, ddg_fetch, create_app, _clamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app):
    """Wrap a FastMCP app's lifespan context so we can test it."""
    async with app.lifespan(app):
        yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_env():
    """Ensure predictable env for every test."""
    old = os.environ.copy()
    os.environ.setdefault("DDGS_TIMEOUT", "5")
    os.environ.pop("DDGS_PROXY", None)
    os.environ.pop("MCP_API_TOKEN", None)
    yield
    os.environ.clear()
    os.environ.update(old)


@pytest.fixture
def mock_text():
    return [
        {"title": "Python", "href": "https://python.org/", "body": "The official home."},
        {"title": "Python Docs", "href": "https://docs.python.org/", "body": "Docs."},
    ]


@pytest.fixture
def mock_news():
    return [{
        "date": "2025-06-01T12:00:00Z", "title": "Tech News",
        "body": "Content.", "url": "https://ex.com/1",
        "image": "https://ex.com/img.jpg", "source": "Tech News",
    }]


@pytest.fixture
def mock_images():
    return [{
        "title": "Sunset", "image": "https://ex.com/sunset.jpg",
        "thumbnail": "https://ex.com/t.jpg", "url": "https://ex.com/p",
        "height": 1080, "width": 1920, "source": "Ex Photos",
    }]


@pytest.fixture
def mock_videos():
    return [{
        "content": "https://youtube.com/watch?v=abc", "description": "Tutorial.",
        "duration": "10:30", "title": "How to Code", "uploader": "Academy",
        "published": "2025-05-15T08:00:00Z", "provider": "YouTube",
        "statistics": {"viewCount": 15000},
        "images": {"large": "https://ex.com/thumb.jpg"},
    }]


# ===================================================================
# Tool unit tests
# ===================================================================

@patch("server._make_ddgs")
def test_ddg_search_basic(mock_factory, mock_text):
    mock_factory.return_value.text.return_value = mock_text
    results = ddg_search(query="python")
    assert len(results) == 2
    assert results[0]["title"] == "Python"


@patch("server._make_ddgs")
def test_ddg_search_clamps_max_results(mock_factory):
    mock_factory.return_value.text.return_value = []
    ddg_search(query="test", max_results=500)
    assert mock_factory.return_value.text.call_args[1]["max_results"] == 50


@patch("server._make_ddgs")
def test_ddg_search_min_one(mock_factory):
    mock_factory.return_value.text.return_value = []
    ddg_search(query="test", max_results=0)
    assert mock_factory.return_value.text.call_args[1]["max_results"] == 1


@patch("server._make_ddgs")
def test_ddg_search_passes_filters(mock_factory):
    mock_factory.return_value.text.return_value = []
    ddg_search(query="test", region="us-en", safesearch="off", timelimit="w")
    kw = mock_factory.return_value.text.call_args[1]
    assert kw["region"] == "us-en"
    assert kw["safesearch"] == "off"
    assert kw["timelimit"] == "w"


@patch("server._make_ddgs")
def test_ddg_news_basic(mock_factory, mock_news):
    mock_factory.return_value.news.return_value = mock_news
    results = ddg_news(query="tech")
    assert results[0]["title"] == "Tech News"
    assert results[0]["source"] == "Tech News"


@patch("server._make_ddgs")
def test_ddg_news_clamps(mock_factory):
    mock_factory.return_value.news.return_value = []
    ddg_news(query="test", max_results=999)
    assert mock_factory.return_value.news.call_args[1]["max_results"] == 50


@patch("server._make_ddgs")
def test_ddg_images_basic(mock_factory, mock_images):
    mock_factory.return_value.images.return_value = mock_images
    results = ddg_images(query="sunset")
    assert results[0]["width"] == 1920


@patch("server._make_ddgs")
def test_ddg_images_filters(mock_factory):
    mock_factory.return_value.images.return_value = []
    ddg_images(query="t", size="Large", color="Monochrome", type_image="photo")
    kw = mock_factory.return_value.images.call_args[1]
    assert kw["size"] == "Large" and kw["color"] == "Monochrome"


@patch("server._make_ddgs")
def test_ddg_images_clamps_to_100(mock_factory):
    mock_factory.return_value.images.return_value = []
    ddg_images(query="test", max_results=500)
    assert mock_factory.return_value.images.call_args[1]["max_results"] == 100


@patch("server._make_ddgs")
def test_ddg_videos_basic(mock_factory, mock_videos):
    mock_factory.return_value.videos.return_value = mock_videos
    results = ddg_videos(query="tutorial")
    assert results[0]["uploader"] == "Academy"


@patch("server._make_ddgs")
def test_ddg_videos_passes_filters(mock_factory):
    mock_factory.return_value.videos.return_value = []
    ddg_videos(query="test", resolution="high", duration="medium")
    kw = mock_factory.return_value.videos.call_args[1]
    assert kw["resolution"] == "high" and kw["duration"] == "medium"


@patch("server._make_ddgs")
def test_extra_keys_stripped(mock_factory):
    mock_factory.return_value.text.return_value = [
        {"title": "G", "href": "https://x.com", "body": "ok", "_raw": "secret"},
    ]
    results = ddg_search(query="test")
    assert "_raw" not in results[0]
    assert "title" in results[0]


# ===================================================================
# Integration tests
# ===================================================================

@pytest.mark.asyncio
async def test_health_check():
    app = create_app()
    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_mcp_tools_list():
    app = create_app()
    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )
    assert resp.status_code == 200
    # SSE streaming response — check it contains tool names
    body = resp.text
    assert "ddg_search" in body
    assert "ddg_news" in body
    assert "ddg_images" in body
    assert "ddg_videos" in body
    assert "ddg_fetch" in body


# ===================================================================
# ddg_fetch tests
# ===================================================================

@patch("server.httpx.Client")
@patch("server.trafilatura.extract")
def test_ddg_fetch_basic(mock_extract, mock_client):
    """Mock HTTP and trafilatura to test ddg_fetch logic."""
    mock_extract.return_value = "Extracted article content here."
    mock_response = MagicMock()
    mock_response.text = "<html><title>Test Page</title><body>Content</body></html>"
    mock_response.content = b"<html><body>Content</body></html>"
    mock_response.url = "https://example.com/page"
    mock_response.headers = {"content-type": "text/html"}
    mock_response.raise_for_status = MagicMock()
    mock_client.return_value.__enter__.return_value.get.return_value = mock_response

    result = ddg_fetch(url="https://example.com/page")

    assert result["title"] == "Extracted article content here."
    assert result["url"] == "https://example.com/page"
    assert result["content_type"] == "text/html"


@patch("server.httpx.Client")
@patch("server.trafilatura.extract")
def test_ddg_fetch_url_scheme_added(mock_extract, mock_client):
    """ddg_fetch auto-prepends https:// if missing."""
    mock_extract.return_value = "Content"
    mock_response = MagicMock()
    mock_response.text = "<html><title>X</title></html>"
    mock_response.content = b"<html></html>"
    mock_response.url = "https://example.com"
    mock_response.headers = {"content-type": "text/html"}
    mock_response.raise_for_status = MagicMock()
    mock_client.return_value.__enter__.return_value.get.return_value = mock_response

    result = ddg_fetch(url="example.com")
    assert "https://example.com" in result["url"]


@patch("server.httpx.Client")
@patch("server.trafilatura.extract")
def test_ddg_fetch_handles_http_error(mock_extract, mock_client):
    """ddg_fetch returns error dict on HTTP failure."""
    from httpx import HTTPStatusError, Request

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.reason_phrase = "Not Found"
    mock_response.raise_for_status.side_effect = HTTPStatusError(
        "404", request=Request("GET", "https://example.com/bad"), response=mock_response
    )
    mock_client.return_value.__enter__.return_value.get.return_value = mock_response

    result = ddg_fetch(url="https://example.com/bad")
    assert "HTTP 404" in result["text"]
    assert result["content_type"] == "error"


@pytest.mark.asyncio
async def test_auth_rejects_when_enabled():
    os.environ["MCP_API_TOKEN"] = "secret-token"
    import importlib
    import server as srv
    importlib.reload(srv)

    app = srv.create_app()
    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Wrong token -> 401
            resp = await client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer wrong",
                },
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )
            assert resp.status_code == 401

            # Valid token -> 200
            resp = await client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer secret-token",
                },
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )
            assert resp.status_code == 200
