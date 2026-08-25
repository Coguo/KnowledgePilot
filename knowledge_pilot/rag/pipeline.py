"""RAG 流水线：一次研究任务内的「建库 + 检索 → 带来源上下文」。

`enrich_search` 是 agent 侧的接入点：search_web 工具执行后调用它，
对搜索结果自动抓取建库并做向量检索，返回追加的带来源片段。
"""

from knowledge_pilot.rag.chunker import Chunker
from knowledge_pilot.rag.embedder import Embedder
from knowledge_pilot.rag.fetcher import PageFetcher
from knowledge_pilot.rag.ingestion import ingest_from_results
from knowledge_pilot.rag.retriever import Retriever
from knowledge_pilot.rag.store import VectorStore
from knowledge_pilot.search.base import SearchResult


class RAGPipeline:
    """研究任务粒度的 RAG 组件组合。"""

    def __init__(
        self,
        *,
        fetcher: PageFetcher,
        chunker: Chunker,
        embedder: Embedder,
        store: VectorStore,
        top_k: int = 3,
        max_fetch_urls: int = 3,
        min_text_len: int = 100,
    ) -> None:
        self._fetcher = fetcher
        self._chunker = chunker
        self._embedder = embedder
        self._store = store
        self._retriever = Retriever(embedder=embedder, store=store, top_k=top_k)
        self._max_fetch_urls = max_fetch_urls
        self._min_text_len = min_text_len

    async def enrich_search(
        self, query: str, results: list[SearchResult]
    ) -> str:
        """抓取搜索结果建库，检索 query，返回带来源的追加片段（无命中则空串）。"""
        await ingest_from_results(
            results,
            fetcher=self._fetcher,
            chunker=self._chunker,
            embedder=self._embedder,
            store=self._store,
            max_urls=self._max_fetch_urls,
            min_text_len=self._min_text_len,
        )
        hits = await self._retriever.retrieve(query)
        if not hits:
            return ""
        return "\n\n【知识库检索结果】\n" + self._retriever.format_context(hits)
