"""Stub 搜索：返回结果符合 SearchResult schema，数量与 top_k 一致。"""

from knowledge_pilot.search.base import SearchResult
from knowledge_pilot.search.stub import StubSearchProvider


async def test_stub_returns_expected_schema():
    provider = StubSearchProvider()
    results = await provider.search("测试查询", top_k=3)

    assert len(results) == 3
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.title and r.url and r.snippet and r.content for r in results)
    assert "测试查询" in results[0].title


async def test_stub_factory_registered():
    from knowledge_pilot.search import create_search_provider

    provider = create_search_provider("stub")
    assert provider.name == "stub"
