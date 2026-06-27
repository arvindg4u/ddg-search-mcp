"""Tests for the DuckDuckGo Search MCP server."""

import os
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from server import ddg_search, ddg_news, ddg_images, ddg_videos, app


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
        {
            "title": "Python Programming Language",
            "href": "https://www.python.org/",
            "body": "The official home of the Python programming language.",
        },
        {
            "title": "Python Docs",
            "href": "https://docs.python.org/",
            "body": "Documentation for Python.",
        },
    ]


@pytest.fixture
def mock_news():
    return [
        {
            "date": "2025-06-01T12:00:00Z",
            "title": "Breaking Tech News",
            "body": "A major tech breakthrough was announced today.",
            "url": "https://example.com/news/1",
            "image": "https://example.com/img.jpg",
            "source": "Tech News Network",
        },
    ]


@pytest.fixture
def mock_images():
    return [
        {
            "title": "Sunset Landscape",
            "image": "https://example.com/sunset.jpg",
            "thumbnail": "https://example.com/sunset_thumb.jpg",
            "url": "https://example.com/photo/sunset",
            "height": 1080,
            "width": 1920,
            "source": "Example Photos",
        },
    ]


@pytest.fixture
def mock_videos():
    return [
        {
            "content": "https://www.youtube.com/watch?v=abc123",
            "description": "A tutorial video.",
            "duration": "10:30",
            "title": "How to Code",
            "uploader": "Code Academy",
            "published": "2025-05-15T08:00:00Z",
            "provider": "YouTube",
            "statistics": {"viewCount": 15000},
            "images": {"large": "https://example.com/thumb.jpg"},
        },
    ]


# ===================================================================
# Tool unit tests
# ===================================================================

@patch("server._make_ddgs")
def test_ddg_search_basic(mock_factory, mock_text):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.text.return_value = mock_text
    results = ddg_search(query="python programming")
    assert len(results) == 2
    assert results[0]["title"] == "Python Programming Language"
    assert results[0]["href"] == "https://www.python.org/"


@patch("server._make_ddgs")
def test_ddg_search_clamps_max_results(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.text.return_value = []
    ddg_search(query="test", max_results=500)
    assert ddgs_instance.text.call_args[1]["max_results"] == 50


@patch("server._make_ddgs")
def test_ddg_search_min_one(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.text.return_value = []
    ddg_search(query="test", max_results=0)
    assert ddgs_instance.text.call_args[1]["max_results"] == 1


@patch("server._make_ddgs")
def test_ddg_search_passes_filters(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.text.return_value = []
    ddg_search(query="test", region="us-en", safesearch="off", timelimit="w")
    kw = ddgs_instance.text.call_args[1]
    assert kw["region"] == "us-en"
    assert kw["safesearch"] == "off"
    assert kw["timelimit"] == "w"


@patch("server._make_ddgs")
def test_ddg_news_basic(mock_factory, mock_news):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.news.return_value = mock_news
    results = ddg_news(query="technology")
    assert len(results) == 1
    assert results[0]["title"] == "Breaking Tech News"
    assert results[0]["source"] == "Tech News Network"


@patch("server._make_ddgs")
def test_ddg_news_clamps(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.news.return_value = []
    ddg_news(query="test", max_results=999)
    assert ddgs_instance.news.call_args[1]["max_results"] == 50


@patch("server._make_ddgs")
def test_ddg_images_basic(mock_factory, mock_images):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.images.return_value = mock_images
    results = ddg_images(query="sunset landscape")
    assert len(results) == 1
    assert results[0]["title"] == "Sunset Landscape"
    assert results[0]["width"] == 1920


@patch("server._make_ddgs")
def test_ddg_images_filters(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.images.return_value = []
    ddg_images(query="test", size="Large", color="Monochrome", type_image="photo")
    kw = ddgs_instance.images.call_args[1]
    assert kw["size"] == "Large"
    assert kw["color"] == "Monochrome"
    assert kw["type_image"] == "photo"


@patch("server._make_ddgs")
def test_ddg_images_clamps_to_100(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.images.return_value = []
    ddg_images(query="test", max_results=500)
    assert ddgs_instance.images.call_args[1]["max_results"] == 100


@patch("server._make_ddgs")
def test_ddg_videos_basic(mock_factory, mock_videos):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.videos.return_value = mock_videos
    results = ddg_videos(query="coding tutorial")
    assert len(results) == 1
    assert results[0]["uploader"] == "Code Academy"
    assert results[0]["provider"] == "YouTube"


@patch("server._make_ddgs")
def test_ddg_videos_passes_filters(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.videos.return_value = []
    ddg_videos(query="test", resolution="high", duration="medium")
    kw = ddgs_instance.videos.call_args[1]
    assert kw["resolution"] == "high"
    assert kw["duration"] == "medium"


@patch("server._make_ddgs")
def test_extra_keys_stripped(mock_factory):
    ddgs_instance = mock_factory.return_value
    ddgs_instance.text.return_value = [
        {"title": "Good", "href": "https://x.com", "body": "ok", "_raw": "secret"},
    ]
    results = ddg_search(query="test")
    assert "_raw" not in results[0]
    assert "title" in results[0]


# ===================================================================
# Integration tests: health check and auth
# ===================================================================

@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_rejects_when_enabled():
    os.environ["MCP_API_TOKEN"] = "secret-token"
    import importlib
    import server as srv
    importlib.reload(srv)

    transport = ASGITransport(app=srv.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Wrong token → 401
        resp = await client.get("/mcp", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

        # Valid token → not 401
        resp = await client.get("/mcp", headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code != 401

    os.environ.pop("MCP_API_TOKEN", None)
