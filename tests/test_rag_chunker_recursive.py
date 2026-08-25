"""RecursiveChunker / create_chunker：分节保持、重叠不变量、尺寸上界、元数据透传。"""

import pytest

from knowledge_pilot.rag.chunker import (
    FixedSizeChunker,
    RecursiveChunker,
    create_chunker,
)
from knowledge_pilot.rag.documents import Document


def _doc(
    text: str, url: str = "https://example.com/a", metadata: dict | None = None
) -> Document:
    return Document(
        document_id="abc123",
        source="web",
        url=url,
        title="标题",
        text=text,
        metadata=metadata or {},
    )


def test_short_text_single_chunk():
    chunker = RecursiveChunker(chunk_size=800, overlap=200)
    chunks = chunker.chunk(_doc("短文本" * 10))

    assert len(chunks) == 1
    c = chunks[0]
    assert c.chunk_id == "abc123:0"
    assert c.document_id == "abc123"
    assert c.url == "https://example.com/a"
    assert c.title == "标题"
    assert c.source == "web"
    assert c.metadata["chunk_index"] == "0"
    assert c.metadata["chunk_total"] == "1"


def test_sections_stay_whole():
    """含 \n\n 分节、每节 ≤ chunk_size 的文本：任何分节都完整落在某个 chunk 内，
    不会被从中切断（recursive 优于 fixed 的演示点）。"""
    sections = [
        "第一章：介绍 RAG 的基础概念。",
        "第二章：向量检索与 BM25 混合搜索。",
        "第三章：重排序器的引入动机。",
    ]
    text = "\n\n".join(sections)
    chunker = RecursiveChunker(chunk_size=200, overlap=50)
    chunks = chunker.chunk(_doc(text))

    joined = [c.text for c in chunks]
    for sec in sections:
        assert any(sec in c for c in joined), f"分节被切断: {sec!r}"


def test_long_text_char_split_terminates_and_sizes():
    """无分隔符的长文本走字符兜底切分：能终止，且每块 ≤ chunk_size + overlap。"""
    text = "长" * 5000  # 无任何分隔符
    chunker = RecursiveChunker(chunk_size=200, overlap=50)
    chunks = chunker.chunk(_doc(text))

    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 200 + 50, f"块超尺寸上界: {len(c.text)}"


def test_overlap_invariant():
    """相邻 chunk 共享的字符应完全一致（可还原边界上下文）。"""
    text = "".join(f"这是第{i}句，包含一些检索相关的内容。" for i in range(200))
    chunker = RecursiveChunker(chunk_size=200, overlap=50)
    chunks = chunker.chunk(_doc(text))

    assert len(chunks) > 1
    for i in range(len(chunks) - 1):
        # 块足够长时，后块开头应精确等于前块尾部 overlap 字符
        if len(chunks[i].text) > 50:
            assert chunks[i].text[-50:] == chunks[i + 1].text[:50]


def test_metadata_propagated_to_chunks():
    """文档级元数据（如 search_score）应透传到每个 chunk。"""
    chunker = RecursiveChunker(chunk_size=200, overlap=50)
    chunks = chunker.chunk(_doc("内容" * 500, metadata={"search_score": "0.95"}))

    assert chunks
    for c in chunks:
        assert c.metadata["search_score"] == "0.95"
        assert c.metadata["chunk_total"] == str(len(chunks))


def test_overlap_must_be_less_than_chunk_size():
    with pytest.raises(ValueError):
        RecursiveChunker(chunk_size=100, overlap=100)


def test_empty_text_returns_no_chunks():
    chunker = RecursiveChunker()
    assert chunker.chunk(_doc("   ")) == []


def test_create_chunker_strategies():
    assert isinstance(create_chunker("fixed"), FixedSizeChunker)
    assert isinstance(create_chunker("recursive"), RecursiveChunker)
    with pytest.raises(ValueError):
        create_chunker("semantic")
