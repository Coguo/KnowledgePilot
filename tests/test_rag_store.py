"""ChromaStore：内存客户端（EphemeralClient）下的 add / search / count。"""

import pytest

pytest.importorskip("chromadb")

from knowledge_pilot.rag.documents import Chunk
from knowledge_pilot.rag.store import ChromaStore


def _chunk(i: int, text: str) -> Chunk:
    return Chunk(
        document_id="doc1",
        chunk_id=f"doc1:{i}",
        source="web",
        url="https://example.com",
        title="标题",
        text=text,
        metadata={"chunk_index": str(i), "chunk_total": "2"},
    )


def _vec(i: int, dims: int = 4) -> list[float]:
    # 不同 i 得到不同的归一化向量；同 i 恒等
    base = [float(i + 1), 1.0, 0.0, 0.0]
    norm = sum(x * x for x in base) ** 0.5
    return [x / norm for x in base]


async def test_add_search_count():
    store = ChromaStore()  # 空 persist_dir → EphemeralClient（内存，不写盘）
    chunks = [_chunk(0, "苹果是水果"), _chunk(1, "香蕉是水果")]
    await store.add_chunks(chunks, [_vec(0), _vec(1)])

    assert await store.count() == 2

    hits = await store.search(_vec(0), top_k=1)
    assert len(hits) == 1
    assert hits[0].chunk.chunk_id == "doc1:0"
    assert hits[0].chunk.url == "https://example.com"
    assert hits[0].chunk.text == "苹果是水果"
    assert hits[0].score > 0.9  # 余弦距离转相似度


async def test_search_empty_returns_empty():
    store = ChromaStore()
    assert await store.search([1.0, 0.0, 0.0, 0.0], top_k=3) == []


async def test_search_top_k_limits(tmp_path):
    store = ChromaStore()
    chunks = [_chunk(i, f"文本 {i}") for i in range(5)]
    await store.add_chunks(chunks, [_vec(i) for i in range(5)])

    hits = await store.search(_vec(0), top_k=2)
    assert len(hits) == 2
