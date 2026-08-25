"""ingest_from_results：抓取建库 / 失败回退摘要 / 太短跳过。"""

from knowledge_pilot.rag.chunker import FixedSizeChunker
from knowledge_pilot.rag.fetcher import FetchedDocument
from knowledge_pilot.rag.ingestion import ingest_from_results
from knowledge_pilot.search.base import SearchResult

from tests.fakes import FakeEmbedder, InMemoryVectorStore, StubFetcher


def _result(title: str, url: str, content: str | None) -> SearchResult:
    return SearchResult(title=title, url=url, snippet="", content=content)


async def test_ingest_fetched_documents():
    fetcher = StubFetcher(
        {
            "https://a.example": FetchedDocument(
                url="https://a.example", title="A 页面", text="A" * 500
            ),
            "https://b.example": FetchedDocument(
                url="https://b.example", title="B 页面", text="B" * 500
            ),
        }
    )
    store = InMemoryVectorStore()
    results = [
        _result("A", "https://a.example", "回退摘要A"),
        _result("B", "https://b.example", "回退摘要B"),
        _result("C", "https://c.example", None),  # 无正文且抓不到 → 跳过
    ]

    report = await ingest_from_results(
        results,
        fetcher=fetcher,
        chunker=FixedSizeChunker(),
        embedder=FakeEmbedder(),
        store=store,
    )

    assert report.documents == 2
    assert report.skipped == 1
    assert report.chunks == await store.count()
    assert report.chunks > 0


async def test_ingest_falls_back_to_snippet_content():
    fetcher = StubFetcher({}, fail_urls={"https://a.example"})
    store = InMemoryVectorStore()
    results = [_result("A", "https://a.example", "摘要正文" * 50)]  # 500 字符

    report = await ingest_from_results(
        results,
        fetcher=fetcher,
        chunker=FixedSizeChunker(),
        embedder=FakeEmbedder(),
        store=store,
    )

    assert report.documents == 1
    assert await store.count() > 0


async def test_ingest_skips_too_short_text():
    fetcher = StubFetcher(
        {
            "https://a.example": FetchedDocument(
                url="https://a.example", title="A", text="太短了"
            )
        }
    )
    store = InMemoryVectorStore()
    results = [_result("A", "https://a.example", None)]

    report = await ingest_from_results(
        results,
        fetcher=fetcher,
        chunker=FixedSizeChunker(),
        embedder=FakeEmbedder(),
        store=store,
        min_text_len=100,
    )

    assert report.skipped == 1
    assert await store.count() == 0
