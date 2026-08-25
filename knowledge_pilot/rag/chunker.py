"""文本分块：把整篇文档切成可检索的 Chunk。

Phase 1 用 fixed-size + overlap（确定性、可精确测试），
作为 Phase 2 对比实验（fixed / recursive / semantic）的基线。
"""

from typing import Protocol

from knowledge_pilot.rag.documents import Chunk, Document


class Chunker(Protocol):
    """分块器契约：Phase 2 新增 recursive / semantic 只加类，不改流水线。"""

    def chunk(self, document: Document) -> list[Chunk]:
        """把一篇文档切成若干 Chunk（空文档返回 []）。"""
        ...


class FixedSizeChunker:
    """定长分块，相邻块重叠 overlap 个字符以保留边界上下文。"""

    def __init__(self, chunk_size: int = 800, overlap: int = 200) -> None:
        if overlap >= chunk_size:
            raise ValueError(f"overlap({overlap}) 必须小于 chunk_size({chunk_size})")
        if chunk_size <= 0 or overlap < 0:
            raise ValueError("chunk_size 必须为正数，overlap 不能为负数")
        self._chunk_size = chunk_size
        self._overlap = overlap

    def chunk(self, document: Document) -> list[Chunk]:
        text = document.text.strip()
        if not text:
            return []

        chunks: list[Chunk] = []
        step = self._chunk_size - self._overlap
        start = 0
        index = 0
        while start < len(text):
            end = min(start + self._chunk_size, len(text))
            piece = text[start:end]
            if piece.strip():
                chunks.append(
                    Chunk(
                        document_id=document.document_id,
                        chunk_id=f"{document.document_id}:{index}",
                        source=document.source,
                        url=document.url,
                        title=document.title,
                        text=piece,
                        metadata={
                            "chunk_index": str(index),
                            "chunk_total": "",  # 切完后统一回填
                        },
                    )
                )
            index += 1
            if end >= len(text):
                break
            start += step

        total = str(len(chunks))
        for c in chunks:
            c.metadata["chunk_total"] = total
        return chunks
