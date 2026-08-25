"""PageFetcher：假 httpx 客户端，验证正文提取 / 失败静默 / 并发部分成功。"""

import pytest

pytest.importorskip("trafilatura")

import httpx

from knowledge_pilot.rag.fetcher import PageFetcher

HTML = (
    "<html><head><title>示例标题</title></head>"
    "<body><nav>导航菜单</nav><main>"
    "<p>第一段内容。</p><p>第二段内容。</p>"
    "</main></body></html>"
)


class _FakeResponse:
    def __init__(self, status: int = 200, text: str = ""):
        self.status_code = status
        self.text = text


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.get_url: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str):
        self.get_url = url
        return self._response


async def test_fetch_extracts_text_and_title(monkeypatch):
    fake = _FakeAsyncClient(_FakeResponse(200, HTML))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)

    doc = await PageFetcher().fetch("https://example.com/a")

    assert doc is not None
    assert doc.title == "示例标题"
    assert "第一段内容" in doc.text
    assert "导航菜单" not in doc.text  # 去导航
    assert fake.get_url == "https://example.com/a"


async def test_fetch_non_200_returns_none(monkeypatch):
    fake = _FakeAsyncClient(_FakeResponse(404, "<html></html>"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)

    assert await PageFetcher().fetch("https://example.com/404") is None


async def test_fetch_http_error_returns_none(monkeypatch):
    class _BoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise httpx.HTTPError("boom")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _BoomClient())

    assert await PageFetcher().fetch("https://example.com/x") is None


async def test_fetch_many_skips_failures(monkeypatch):
    ok = _FakeAsyncClient(_FakeResponse(200, HTML))
    fail = _FakeAsyncClient(_FakeResponse(404, "<html></html>"))
    clients = [ok, fail, ok]
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: clients.pop(0))

    docs = await PageFetcher().fetch_many(
        ["https://a.example", "https://b.example", "https://c.example"]
    )

    assert len(docs) == 2  # 失败项被跳过
