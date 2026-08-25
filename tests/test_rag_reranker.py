"""重排序器：FakeReranker 行为 / CrossEncoderReranker 懒加载与单例。"""

import sys

from knowledge_pilot.rag.documents import Chunk
from knowledge_pilot.rag.reranker import CrossEncoderReranker, get_shared_reranker
from knowledge_pilot.rag.store import SearchHit
from tests.fakes import FakeReranker


def _hit(chunk_id: str, text: str) -> SearchHit:
    return SearchHit(
        chunk=Chunk(
            document_id="doc1",
            chunk_id=chunk_id,
            source="web",
            url="https://example.com/a",
            title="标题",
            text=text,
            metadata={"chunk_index": "0", "chunk_total": "1"},
        ),
        score=1.0,
    )


def test_fake_reranker_reorders_and_truncates():
    reranker = FakeReranker()
    hits = [
        _hit("a", "苹果是一种水果"),
        _hit("b", "苹果 苹果 苹果 苹果"),
    ]
    ranked = reranker.rerank("苹果", hits, top_k=1)

    assert len(ranked) == 1
    assert ranked[0].chunk.chunk_id == "b"  # 命中次数多者优先
    assert reranker.calls == [2]


def test_fake_reranker_tie_keeps_original_order():
    reranker = FakeReranker()
    hits = [_hit("a", "苹果"), _hit("b", "苹果")]
    ranked = reranker.rerank("苹果", hits, top_k=2)
    assert [h.chunk.chunk_id for h in ranked] == ["a", "b"]


def test_cross_encoder_construction_is_lazy(monkeypatch):
    """构造 CrossEncoderReranker 不应触发 sentence-transformers 导入。"""
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    reranker = CrossEncoderReranker()
    assert "sentence_transformers" not in sys.modules
    # 空候选直接返回，也不触发加载
    assert reranker.rerank("查询", [], top_k=3) == []


def test_get_shared_reranker_singleton():
    class _Cfg:
        rag_rerank_model = "BAAI/bge-reranker-base"
        embedding_cache_dir = ""
        embedding_device = "cpu"

    a = get_shared_reranker(_Cfg())
    b = get_shared_reranker(_Cfg())
    assert a is b
