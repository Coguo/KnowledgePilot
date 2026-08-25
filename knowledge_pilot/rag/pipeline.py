"""RAG 流水线：一次研究任务内的「建库 + 检索 → 带来源上下文」。

`enrich_search` 是 agent 侧的接入点：search_web 工具执行后调用它，
对搜索结果自动抓取建库并检索，返回追加的带来源片段。

Phase 2 在此叠加三个可插拔优化：
- `retriever`：向量 / 向量+BM25 混合（RRF 融合），由工厂装配。
- `reranker`：对候选池做 CrossEncoder 精排。
- `rewriter`：检索前用 LLM 改写查询（默认关闭，省一次 LLM 调用）。
所有环节都可选，RAG 关闭路径逐字节不变。
"""

import asyncio

from knowledge_pilot.rag.chunker import Chunker
from knowledge_pilot.rag.embedder import Embedder
from knowledge_pilot.rag.fetcher import PageFetcher
from knowledge_pilot.rag.ingestion import ingest_from_results
from knowledge_pilot.rag.lexical import LexicalIndex
from knowledge_pilot.rag.reranker import Reranker
from knowledge_pilot.rag.retriever import Retriever, format_hits_context
from knowledge_pilot.rag.rewrite import QueryRewriter
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
        retriever: Retriever | None = None,
        lexical: LexicalIndex | None = None,
        reranker: Reranker | None = None,
        rewriter: QueryRewriter | None = None,
        top_k: int = 3,
        rerank_candidates: int = 20,
        max_fetch_urls: int = 3,
        min_text_len: int = 100,
    ) -> None:
        self._fetcher = fetcher
        self._chunker = chunker
        self._embedder = embedder
        self._store = store
        # 未显式传入时退回纯向量检索（兼容直接构造 / 测试注入）
        self._retriever = retriever or Retriever(
            embedder=embedder, store=store, top_k=top_k
        )
        self._lexical = lexical
        self._reranker = reranker
        self._rewriter = rewriter
        self._top_k = top_k
        self._rerank_candidates = rerank_candidates
        self._max_fetch_urls = max_fetch_urls
        self._min_text_len = min_text_len

    async def enrich_search(
        self, query: str, results: list[SearchResult]
    ) -> str:
        """抓取搜索结果建库，检索 query，返回带来源的追加片段（无命中则空串）。

        检索链路：改写查询（可选）→ 候选池检索 → 精排（可选）→ 截断 top_k。
        """
        await ingest_from_results(
            results,
            fetcher=self._fetcher,
            chunker=self._chunker,
            embedder=self._embedder,
            store=self._store,
            lexical=self._lexical,
            max_urls=self._max_fetch_urls,
            min_text_len=self._min_text_len,
        )

        candidate = await self._rewriter.rewrite(query) if self._rewriter else query
        hits = await self._retriever.retrieve(
            candidate, top_k=self._rerank_candidates
        )
        if self._reranker:
            hits = await asyncio.to_thread(
                self._reranker.rerank, candidate, hits, top_k=self._top_k
            )
        else:
            hits = hits[: self._top_k]

        if not hits:
            return ""
        return "\n\n【知识库检索结果】\n" + format_hits_context(hits)

    def close(self) -> None:
        """任务结束清理临时知识库（删除 task_{uuid} collection），幂等。"""
        close = getattr(self._store, "delete_collection", None)
        if close is not None:
            close()
