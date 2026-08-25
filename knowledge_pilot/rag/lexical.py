"""词法检索索引：BM25（关键词精确匹配），与向量检索互补，做混合搜索。

- `tokenize`：轻量 CJK 分词——拉丁/数字串整词保留，中文按双字重叠切分，
  免 jieba 依赖即可支持中英混合文本的 BM25 打分。
- `LexicalIndex` Protocol：检索/测试用注入点（Bm25Index 或 Fake）。
- `Bm25Index`：rank-bm25 实现。`rank_bm25` 只在 `search` 内懒加载，
  未安装 `[rag]` extra 时本模块可正常 import。
"""

import re
from typing import Protocol

from knowledge_pilot.rag.documents import Chunk
from knowledge_pilot.rag.store import SearchHit


# CJK 范围：汉字 + 假名 + 谚文
_CJK_RANGE = "一-鿿぀-ヿ가-힯"
_CJK_RUN = re.compile(f"[{_CJK_RANGE}]+")
_LATIN_WORD = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """把文本切分为 BM25 的 token 序列。

    - 拉丁 / 数字串：整词保留并小写（"RAG" → ["rag"]）。
    - CJK 连续段：按双字重叠切（"混合搜索" → ["混合", "合搜", "搜索"]），
      单字段落保留单字。双字重叠比单字更能表达词组，且零额外依赖。
    """
    tokens: list[str] = []
    for match in _LATIN_WORD.finditer(text):
        tokens.append(match.group(0).lower())
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class LexicalIndex(Protocol):
    """词法索引契约：基于关键词的检索（与向量检索互补）。"""

    def add_chunks(self, chunks: list[Chunk]) -> None: ...

    def search(self, query: str, top_k: int) -> list[SearchHit]: ...


class Bm25Index:
    """rank-bm25 实现：`add_chunks` 建库，`search` 打分取 top_k。

    一个任务的知识库很小，采用"改动后整库重建"（dirty 标记）即可，
    无需增量更新；`rank_bm25` 为纯 Python，重建成本可忽略。
    """

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._bm25 = None
        self._dirty = False

    def add_chunks(self, chunks: list[Chunk]) -> None:
        self._chunks.extend(chunks)
        self._dirty = True

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        if not self._chunks:
            return []
        from rank_bm25 import BM25Okapi  # 懒加载：未装 [rag] 也能 import 本模块

        if self._dirty or self._bm25 is None:
            self._bm25 = BM25Okapi([tokenize(c.text) for c in self._chunks])
            self._dirty = False
        scores = self._bm25.get_scores(tokenize(query))
        hits = [
            SearchHit(chunk=c, score=float(s))
            for c, s in zip(self._chunks, scores)
            if s > 0
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
