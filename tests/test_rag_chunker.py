"""FixedSizeChunker：单块 / 多块+overlap / 元数据传播 / 参数校验。"""

import pytest

from knowledge_pilot.rag.chunker import FixedSizeChunker
from knowledge_pilot.rag.documents import Document


def _doc(text: str, url: str = "https://example.com/a") -> Document:
    return Document(
        document_id="abc123",
        source="web",
        url=url,
        title="标题",
        text=text,
    )


def test_short_text_single_chunk():
    chunker = FixedSizeChunker(chunk_size=800, overlap=200)
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


def test_long_text_multiple_chunks_with_overlap():
    text = "".join(f"段{i}" for i in range(300))  # 600 字符
    chunker = FixedSizeChunker(chunk_size=200, overlap=50)
    chunks = chunker.chunk(_doc(text))

    assert len(chunks) > 1
    # 相邻块重叠部分应完全一致（可还原原文）
    for i in range(len(chunks) - 1):
        assert chunks[i].text[-50:] == chunks[i + 1].text[:50]
    # 块间按步长推进
    assert chunks[1].chunk_id == "abc123:1"


def test_overlap_must_be_less_than_chunk_size():
    with pytest.raises(ValueError):
        FixedSizeChunker(chunk_size=100, overlap=100)


def test_empty_text_returns_no_chunks():
    chunker = FixedSizeChunker()
    assert chunker.chunk(_doc("   ")) == []
