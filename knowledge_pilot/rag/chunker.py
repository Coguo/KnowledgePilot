"""文本分块：把整篇文档切成可检索的 Chunk。

Phase 1 用 fixed-size + overlap（确定性、可精确测试），
作为 Phase 2 对比实验（fixed / recursive）的基线。
Phase 2 新增 RecursiveChunker：优先在自然分隔处切分，保留段落/句子语义。
"""

import re
from typing import Protocol

from knowledge_pilot.rag.documents import Chunk, Document


class Chunker(Protocol):
    """分块器契约：Phase 2 新增 recursive 只加类，不改流水线。"""

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
                            **document.metadata,  # 透传文档级元数据（如 search_score）
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


# 递归分块的分隔符优先级：先粗后细，"" 兜底按字符切保证终止。
SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


class RecursiveChunker:
    """递归分块：按自然分隔符递归切到每片 ≤ chunk_size，再把片拼回 chunk。

    与 FixedSizeChunker 不同，不在固定字符窗口硬切，而是在段落 / 句子边界
    切分，尽量保持语义完整；相邻 chunk 仍保留 overlap 重叠以衔接上下文。
    """

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

        leaves = self._recursive_split(text, 0)
        pieces = self._merge(leaves)

        chunks: list[Chunk] = []
        for index, piece in enumerate(pieces):
            if not piece.strip():
                continue
            chunks.append(
                Chunk(
                    document_id=document.document_id,
                    chunk_id=f"{document.document_id}:{index}",
                    source=document.source,
                    url=document.url,
                    title=document.title,
                    text=piece,
                    metadata={
                        **document.metadata,  # 透传文档级元数据（如 search_score）
                        "chunk_index": str(index),
                        "chunk_total": str(len(pieces)),
                    },
                )
            )
        return chunks

    def _recursive_split(self, text: str, idx: int) -> list[str]:
        """按分隔符递归切分，返回一片片 ≤ chunk_size 的"叶"（拼接可还原原文）。"""
        if len(text) <= self._chunk_size:
            return [text]

        sep = SEPARATORS[idx]
        if sep == "":
            # 兜底：按字符硬切，保证任何文本都能终止。
            return [
                text[i : i + self._chunk_size]
                for i in range(0, len(text), self._chunk_size)
            ]
        if sep in text:
            # lookbehind 保留分隔符本身 → 叶拼接可无损还原原文。
            parts = [p for p in re.split(rf"(?<={re.escape(sep)})", text) if p]
            out: list[str] = []
            for p in parts:
                if len(p) <= self._chunk_size:
                    out.append(p)
                else:
                    out.extend(self._recursive_split(p, idx + 1))
            return out
        # 当前分隔符不存在 → 换更细的分隔符。
        return self._recursive_split(text, idx + 1)

    def _merge(self, leaves: list[str]) -> list[str]:
        """把叶拼回 chunk；新块以「上一块尾部 overlap 字符」开头做重叠衔接。"""
        chunks: list[str] = []
        buf = ""
        seed = ""  # 上一真实块的尾部 overlap 字符
        for leaf in leaves:
            if buf == "":
                buf = seed
            if len(buf) + len(leaf) <= self._chunk_size:
                buf += leaf
            else:
                # 只收尾"有内容"的块，避免把裸重叠种子单独落盘。
                if buf != seed:
                    chunks.append(buf)
                if buf != seed:
                    seed = buf[-self._overlap:] if self._overlap > 0 else ""
                # 种子 + 单叶都放不下：种子并入本块，块可略超 chunk_size
                # （上界 chunk_size + overlap）。
                buf = seed + leaf
        if buf.strip():
            chunks.append(buf)
        return chunks


def create_chunker(
    strategy: str, chunk_size: int = 800, overlap: int = 200
) -> Chunker:
    """按策略构造分块器（fixed / recursive），未知策略抛 ValueError。"""
    if strategy == "fixed":
        return FixedSizeChunker(chunk_size, overlap)
    if strategy == "recursive":
        return RecursiveChunker(chunk_size, overlap)
    raise ValueError(f"未知分块策略: {strategy!r}（支持 fixed / recursive）")
