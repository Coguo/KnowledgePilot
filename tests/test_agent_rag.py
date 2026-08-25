"""Agent × RAG 集成：search_web 工具内部透明建库检索，事件协议不变。

Phase 2 追加：混合检索 + 精排 + 查询改写链路在 Agent 循环内的端到端验证。
"""

from knowledge_pilot.agent.events import DoneEvent, TokenEvent, ToolCallEvent, ToolResultEvent
from knowledge_pilot.agent.loop import run_research
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

PAGE_TEXT = "RAG 是检索增强生成。" * 300  # 足够切成多块


class _FakeSearch:
    """返回与 StubFetcher 匹配的固定搜索结果。"""

    name = "fake"

    async def search(self, query: str, top_k: int = 5):
        return [
            SearchResult(
                title="RAG 教程",
                url="https://example.com/a",
                snippet="",
                content=None,
            )
        ]


def _pipeline() -> RAGPipeline:
    fetcher = StubFetcher(
        {
            "https://example.com/a": FetchedDocument(
                url="https://example.com/a", title="RAG 教程", text=PAGE_TEXT
            )
        }
    )
    return RAGPipeline(
        fetcher=fetcher,
        chunker=FixedSizeChunker(),
        embedder=FakeEmbedder(),
        store=InMemoryVectorStore(),
        top_k=2,
    )


async def test_rag_enriches_search_tool_result():
    llm = FakeChatClient(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "RAG"}'}]),
        (["根据知识库，RAG 是检索增强生成。"], []),
    ])
    pipeline = _pipeline()

    events = [
        e async for e in run_research(
            "研究 RAG", llm=llm, search=_FakeSearch(), rag=pipeline
        )
    ]

    # 事件协议不变
    assert [type(e).__name__ for e in events] == [
        "ToolCallEvent", "ToolResultEvent", "TokenEvent", "DoneEvent",
    ]

    # 回填的 tool message 带【知识库检索结果】与来源引用
    messages_round2 = llm.seen_messages[1]
    tool_content = messages_round2[3]["content"]
    assert "【知识库检索结果】" in tool_content
    assert "RAG 教程" in tool_content
    assert "https://example.com/a" in tool_content

    # 已抓取建库
    assert await pipeline._store.count() > 0


def _pipeline_phase2(rewriter_llm=None) -> RAGPipeline:
    """Phase 2 流水线：混合检索 + 精排 + 可选改写，全部用 Fake。"""
    fetcher = StubFetcher(
        {
            "https://example.com/a": FetchedDocument(
                url="https://example.com/a", title="RAG 教程", text=PAGE_TEXT
            )
        }
    )
    embedder = FakeEmbedder()
    store = InMemoryVectorStore()
    lexical = FakeLexicalIndex()
    vector = Retriever(embedder=embedder, store=store, top_k=3)
    hybrid = HybridRetriever(vector, lexical, top_k=5, branch_top_k=5)
    return RAGPipeline(
        fetcher=fetcher,
        chunker=FixedSizeChunker(),
        embedder=embedder,
        store=store,
        retriever=hybrid,
        lexical=lexical,
        reranker=FakeReranker(),
        rewriter=LLMQueryRewriter(rewriter_llm) if rewriter_llm else None,
        top_k=2,
        rerank_candidates=5,
    )


async def test_rag_phase2_hybrid_rerank_rewrite_e2e():
    """端到端：search_web → ingest(带 BM25) → 改写 → 混合检索 → 精排 → 回填。"""
    loop_llm = FakeChatClient(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "RAG"}'}]),
        (["根据知识库回答。"], []),
    ])
    rewriter_llm = FakeChatClient(script=[])
    rewriter_llm.complete_output = "RAG 检索增强生成"
    pipeline = _pipeline_phase2(rewriter_llm)

    events = [
        e async for e in run_research(
            "研究 RAG", llm=loop_llm, search=_FakeSearch(), rag=pipeline
        )
    ]

    # 事件协议不变
    assert [type(e).__name__ for e in events] == [
        "ToolCallEvent", "ToolResultEvent", "TokenEvent", "DoneEvent",
    ]

    # 改写真的被调用（独立 llm 的 seen_messages 被 complete() 写入）
    assert rewriter_llm.seen_messages
    # 精排真的被调用（混合检索给出候选池后重排）
    assert pipeline._reranker.calls
    # 知识库结果照常回填到 tool message
    tool_content = loop_llm.seen_messages[1][3]["content"]
    assert "【知识库检索结果】" in tool_content
    assert "RAG 教程" in tool_content
    assert "https://example.com/a" in tool_content


async def test_rag_none_keeps_phase0_behavior():
    llm = FakeChatClient(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "x"}'}]),
        (["回答。"], []),
    ])

    events = [
        e async for e in run_research(
            "问题", llm=llm, search=_FakeSearch()
        )
    ]

    assert [type(e).__name__ for e in events] == [
        "ToolCallEvent", "ToolResultEvent", "TokenEvent", "DoneEvent",
    ]
    tool_content = llm.seen_messages[1][3]["content"]
    assert "【知识库检索结果】" not in tool_content
