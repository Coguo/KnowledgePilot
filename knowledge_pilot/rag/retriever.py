"""检索器：查询词 → 向量化 → 向量库检索 → 拼带来源上下文。"""

import asyncio

from knowledge_pilot.rag.embedder import Embedder
from knowledge_pilot.rag.store import SearchHit, VectorStore


def format_hits_context(
    hits: list[SearchHit], *, snippet_len: int = 600
) -> str:
    """把命中块格式化为带来源引用的上下文片段（向量/混合/精排后通用）。"""
    parts: list[str] = []
    for h in hits:
        text = h.chunk.text[:snippet_len]
        parts.append(f"[来源: {h.chunk.title} ({h.chunk.url})]\n{text}")
    return "\n\n".join(parts)


class Retriever:
    """把 query 嵌入并与库中 chunk 比较，返回带来源的检索结果。"""

    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        top_k: int = 3,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._top_k = top_k

    async def retrieve(
        self, query: str, *, top_k: int | None = None
    ) -> list[SearchHit]:
        embedding = await asyncio.to_thread(self._embedder.embed, [query])
        return await self._store.search(embedding[0], top_k or self._top_k)

    def format_context(
        self, hits: list[SearchHit], *, snippet_len: int = 600
    ) -> str:
        return format_hits_context(hits, snippet_len=snippet_len)
