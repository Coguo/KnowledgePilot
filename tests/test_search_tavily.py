"""Tavily 搜索：构造校验 + 响应映射（mock httpx，不联网）。"""

import httpx
import pytest

from knowledge_pilot.search.base import SearchResult
from knowledge_pilot.search.tavily import TavilySearchProvider

SAMPLE_RESPONSE = {
    "query": "测试",
    "results": [
        {"title": "第一条", "url": "https://example.com/a", "content": "内容A", "score": 0.9},
        {"title": "第二条", "url": "https://example.com/b", "content": "内容B", "score": 0.8},
    ],
}


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeAsyncClient:
    """假的 httpx.AsyncClient：记录请求，返回预设响应。"""

    def __init__(self, data):
        self._data = data
        self.url = None
        self.payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.url = url
        self.payload = json
        return _FakeResponse(self._data)


def test_requires_api_key():
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        TavilySearchProvider("")


async def test_maps_results(monkeypatch):
    fake = _FakeAsyncClient(SAMPLE_RESPONSE)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)

    provider = TavilySearchProvider("tvly-test")
    results = await provider.search("测试", top_k=5)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].title == "第一条"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "内容A"
    assert results[0].metadata["score"] == "0.9"

    # 请求体应带 api_key / query / max_results
    assert fake.payload["api_key"] == "tvly-test"
    assert fake.payload["query"] == "测试"
    assert fake.payload["max_results"] == 5


def test_factory_registers_tavily():
    from knowledge_pilot.search import create_search_provider

    provider = create_search_provider("tavily", "tvly-test")
    assert provider.name == "tavily"
