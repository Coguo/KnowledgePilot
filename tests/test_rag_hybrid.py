"""混合检索：RRF 融合（纯函数）+ HybridRetriever（向量 + 词法双路）。"""

import pytest

from knowledge_pilot.rag.documents import Chunk
from knowledge_pilot.rag.hybrid import HybridRetriever, _rrf_fuse
from knowledge_pilot.rag.store import SearchHit
from tests.fakes import FakeLexicalIndex


def _hit(chunk_id: str, text: str = "内容") -> SearchHit:
    return SearchHit(
        chunk=_chunk(chunk_id, text),
        score=1.0,
    )


def _chunk(chunk_id: str, text: str = "内容") -> Chunk:
    return Chunk(
        document_id="doc1",
        chunk_id=chunk_id,
        source="web",
        url="https://example.com/a",
        title="标题",
        text=text,
        metadata={"chunk_index": "0", "chunk_total": "1"},
    )


# ---- _rrf_fuse 纯函数 ------------------------------------------------


def test_rrf_fuse_dedupes_and_boosts_two_branch_hits():
    vector = [_hit("a"), _hit("b")]
    lexical = [_hit("b"), _hit("c")]

    fused = _rrf_fuse(vector, lexical)
    ids = [h.chunk.chunk_id for h in fused]

    # b 被两路同时命中 → 双份 1/(k+rank) 加成，排第一；b 只出现一次（去重）
    assert set(ids) == {"a", "b", "c"}
    assert ids[0] == "b"


def test_rrf_fuse_single_branch_chunk_still_appears():
    # 只有词法路命中 c：融合结果仍应包含它（Hybrid 能补向量漏检）
    vector = [_hit("a")]
    lexical = [_hit("c")]

    fused = _rrf_fuse(vector, lexical)
    assert {h.chunk.chunk_id for h in fused} == {"a", "c"}


def test_rrf_fuse_empty_inputs():
    assert _rrf_fuse([], []) == []


# ---- HybridRetriever -------------------------------------------------


class _StubVector:
    """可控的向量检索器替身：返回预设命中序列。"""

    def __init__(self, hits: list[SearchHit]):
        self._hits = hits

    async def retrieve(self, query: str, *, top_k: int | None = None) -> list[SearchHit]:
        return self._hits[: top_k or 3]


@pytest.mark.asyncio
async def test_hybrid_retriever_fuses_and_truncates():
    vector = _StubVector([_hit("a"), _hit("b")])
    lexical = FakeLexicalIndex()
    lexical.add_chunks([_chunk("b", text="xx 股票 行情"), _chunk("c", text="x 基金 收益")])

    hybrid = HybridRetriever(vector, lexical, top_k=2, branch_top_k=10)
    hits = await hybrid.retrieve("x")

    ids = [h.chunk.chunk_id for h in hits]
    assert len(ids) == 2  # top_k 截断
    assert ids[0] == "b"  # 双路命中排第一
    assert "c" in ids or "a" in ids


@pytest.mark.asyncio
async def test_hybrid_retriever_includes_lexical_only_chunk():
    vector = _StubVector([_hit("a")])
    lexical = FakeLexicalIndex()
    lexical.add_chunks([_chunk("b", text="bb 专项 查询"), _chunk("c", text="b 其它 内容")])

    hybrid = HybridRetriever(vector, lexical, top_k=5, branch_top_k=10)
    hits = await hybrid.retrieve("b")

    ids = [h.chunk.chunk_id for h in hits]
    assert "b" in ids  # 词法路命中被融合进来（向量漏检也能找回）
