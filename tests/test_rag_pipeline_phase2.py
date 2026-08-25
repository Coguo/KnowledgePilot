"""RAGPipeline Phase 2：混合/精排/改写路径与 close() 清理。"""

from knowledge_pilot.rag.chunker import FixedSizeChunker
from knowledge_pilot.rag.fetcher import FetchedDocument
from knowledge_pilot.rag.hybrid import HybridRetriever
from knowledge_pilot.rag.pipeline import RAGPipeline
from knowledge_pilot.rag.retriever import Retriever
from knowledge_pilot.rag.rewrite import LLMQueryRewriter
from knowledge_pilot.search.base import SearchResult

from tests.fakes import (
    FakeChatClient,
    FakeEmbedder,
    FakeLexicalIndex,
    FakeReranker,
    InMemoryVectorStore,
    StubFetcher,
)

PAGE_TEXT = "RAG 是检索增强生成。" * 400  # 4800 字符，切多块


def _results() -> list[SearchResult]:
    return [
        SearchResult(
            title="A 页面",
            url="https://a.example",
            snippet="",
            content=None,
        )
    ]


def _fetcher() -> StubFetcher:
    return StubFetcher(
        {
            "https://a.example": FetchedDocument(
                url="https://a.example", title="A 页面", text=PAGE_TEXT
            )
        }
    )


def _hybrid_pipeline(**kwargs) -> RAGPipeline:
    embedder = FakeEmbedder()
    store = InMemoryVectorStore()
    lexical = FakeLexicalIndex()
    vector = Retriever(embedder=embedder, store=store, top_k=3)
    hybrid = HybridRetriever(vector, lexical, top_k=5, branch_top_k=5)
    return RAGPipeline(
        fetcher=_fetcher(),
        chunker=FixedSizeChunker(),
        embedder=embedder,
        store=store,
        retriever=hybrid,
        lexical=lexical,
        top_k=2,
        rerank_candidates=5,
        **kwargs,
    )


async def test_enrich_search_hybrid_rerank_path():
    reranker = FakeReranker()
    pipeline = _hybrid_pipeline(reranker=reranker)

    context = await pipeline.enrich_search("RAG 检索增强", _results())

    assert "【知识库检索结果】" in context
    assert "A 页面" in context
    assert "https://a.example" in context
    assert reranker.calls  # 精排被调用，收到候选池


async def test_enrich_search_hybrid_only_no_rerank():
    pipeline = _hybrid_pipeline()  # 无 reranker → 截断 top_k

    context = await pipeline.enrich_search("RAG 检索增强", _results())
    assert "【知识库检索结果】" in context


async def test_enrich_search_rewrite_path():
    llm = FakeChatClient(script=[])
    llm.complete_output = "改写后的查询"
    rewriter = LLMQueryRewriter(llm)
    pipeline = _hybrid_pipeline(rewriter=rewriter)

    context = await pipeline.enrich_search("原始问题", _results())

    assert llm.seen_messages  # 改写被调用
    assert "【知识库检索结果】" in context


async def test_enrich_search_no_results_no_context():
    """抓取失败 + 无摘要回退 → 空库 → 无命中 → 返回空串。"""
    fetcher = StubFetcher({}, fail_urls={"https://a.example"})
    embedder = FakeEmbedder()
    store = InMemoryVectorStore()
    lexical = FakeLexicalIndex()
    vector = Retriever(embedder=embedder, store=store, top_k=3)
    hybrid = HybridRetriever(vector, lexical, top_k=5, branch_top_k=5)
    pipeline = RAGPipeline(
        fetcher=fetcher,
        chunker=FixedSizeChunker(),
        embedder=embedder,
        store=store,
        retriever=hybrid,
        lexical=lexical,
        top_k=2,
        rerank_candidates=5,
    )

    context = await pipeline.enrich_search(
        "查询",
        [SearchResult(title="A", url="https://a.example", snippet="", content=None)],
    )
    assert context == ""


def test_close_duck_types_delete_collection():
    class _StoreWithClose:
        def __init__(self):
            self.closed = False

        def delete_collection(self) -> None:
            self.closed = True

    store = _StoreWithClose()
    pipeline = RAGPipeline(
        fetcher=_fetcher(),
        chunker=FixedSizeChunker(),
        embedder=FakeEmbedder(),
        store=store,
        top_k=1,
    )
    pipeline.close()
    assert store.closed


def test_close_noop_without_delete_collection():
    store = InMemoryVectorStore()  # 无 delete_collection
    pipeline = RAGPipeline(
        fetcher=_fetcher(),
        chunker=FixedSizeChunker(),
        embedder=FakeEmbedder(),
        store=store,
        top_k=1,
    )
    pipeline.close()  # 不报错
