"""Agent × RAG 集成：search_web 工具内部透明建库检索，事件协议不变。"""

from knowledge_pilot.agent.events import DoneEvent, TokenEvent, ToolCallEvent, ToolResultEvent
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.rag.chunker import FixedSizeChunker
from knowledge_pilot.rag.fetcher import FetchedDocument
from knowledge_pilot.rag.pipeline import RAGPipeline
from knowledge_pilot.search.base import SearchResult

from tests.fakes import FakeChatClient, FakeEmbedder, InMemoryVectorStore, StubFetcher

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
