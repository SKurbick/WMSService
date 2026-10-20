from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs)["href"])


@pytest.mark.asyncio
async def test_browser_homepage_links_resolve():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        parser = Links()
        parser.feed(response.text)
        assert parser.hrefs == ["http://test/docs", "http://test/redoc", "http://test/health"]
        for href in parser.hrefs:
            assert (await client.get(href)).status_code == 200


@pytest.mark.asyncio
async def test_api_homepage_preserves_json():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.json() == {
            "service": app.title, "version": app.version,
            "docs": "/docs", "redoc": "/redoc", "health": "/health",
        }
