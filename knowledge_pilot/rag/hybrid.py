"""混合检索：向量 + BM25 词法，用 Reciprocal Rank Fusion (RRF) 融合排序。

两路检索器的分数量纲不同（余弦相似度 vs BM25），不能直接相加。
RRF 只依赖排名，天然可融合异构检索结果，且无需调权重——这正是
Phase 2 需要 Hybrid Search 的原因：向量抓语义、BM25 抓关键词精确命中，
两者互补（如论文缩写、代码标识符等向量容易失效的场景）。
"""

import asyncio

from knowledge_pilot.rag.lexical import LexicalIndex
from knowledge_pilot.rag.retriever import Retriever
from knowledge_pilot.rag.store import SearchHit


# RRF 标准平滑常数（Cormack et al., 2009）：k=60 抑制尾部的排名噪声。
RRF_K = 60


def _rrf_fuse(
    vector_hits: list[SearchHit],
    lexical_hits: list[SearchHit],
    k: int = RRF_K,
) -> list[SearchHit]:
    """按 chunk_id 合并两路排名：score(d) = Σ 1/(k + rank(d))。

    只取排名（1 起始），忽略两路异构原始分数；被两路同时命中的块排名更高。
    """
    score_by_id: dict[str, float] = {}
    chunk_by_id: dict[str, SearchHit] = {}
    for ranked in (vector_hits, lexical_hits):
        for rank, hit in enumerate(ranked, start=1):
            score_by_id[hit.chunk.chunk_id] = (
                score_by_id.get(hit.chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            )
            chunk_by_id[hit.chunk.chunk_id] = hit
    fused = [
        SearchHit(chunk=chunk_by_id[cid].chunk, score=score_by_id[cid])
        for cid in score_by_id
    ]
    fused.sort(key=lambda h: h.score, reverse=True)
    return fused


class HybridRetriever:
    """同时跑向量与 BM25 两路检索，RRF 融合后取 top_k。

    `branch_top_k`：每路的候选池大小（通常大于最终 top_k），给融合留足素材，
    也与精排的候选数共用同一配置旋钮（rag_rerank_candidates）。
    """

    def __init__(
        self,
        vector: Retriever,
        lexical: LexicalIndex,
        *,
        top_k: int = 3,
        branch_top_k: int = 20,
    ) -> None:
        self._vector = vector
        self._lexical = lexical
        self._top_k = top_k
        self._branch_top_k = branch_top_k

    async def retrieve(
        self, query: str, *, top_k: int | None = None
    ) -> list[SearchHit]:
        vector_hits = await self._vector.retrieve(query, top_k=self._branch_top_k)
        lexical_hits = await asyncio.to_thread(
            self._lexical.search, query, self._branch_top_k
        )
        return _rrf_fuse(vector_hits, lexical_hits)[: top_k or self._top_k]
