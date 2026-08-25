"""离线评测组件：确定性、零重依赖，与 --real 真实组件互换。

与 `tests/fakes.py` 的 Fake 系列同口径（相同文本 → 相同向量 / 相同打分），
但独立于此包内——保证评测代码不依赖 tests 模块，装好 dev 依赖即可离线跑。
"""

import math
import random

from knowledge_pilot.rag.documents import Chunk
from knowledge_pilot.rag.embedder import Embedder
from knowledge_pilot.rag.lexical import LexicalIndex, tokenize
from knowledge_pilot.rag.reranker import Reranker
from knowledge_pilot.rag.store import SearchHit, VectorStore


class DeterministicEmbedder(Embedder):
    """确定性 Embedding：相同文本 → 相同归一化向量（哈希播种）。"""

    dimensions = 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            rng = random.Random(text)  # 由文本哈希播种：可复现
            vec = [rng.random() for _ in range(self.dimensions)]
            norm = math.sqrt(sum(x * x for x in vec))
            out.append([x / norm for x in vec])
        return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryStore:
    """实现 VectorStore Protocol：余弦相似度检索，内存态（评测不用持久化）。"""

    def __init__(self) -> None:
        self._items: list[tuple[Chunk, list[float]]] = []

    async def add_chunks(
        self, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        self._items.extend(zip(chunks, embeddings))

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        where: dict | None = None,
    ) -> list[SearchHit]:
        scored = [(_cosine(query_embedding, emb), c) for c, emb in self._items]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [SearchHit(chunk=c, score=s) for s, c in scored[:top_k]]

    async def count(self) -> int:
        return len(self._items)


def _keyword_score(text: str, query: str) -> float:
    """关键词重叠打分：对查询的每个 token（拉丁词 + CJK 双字重叠）累计出现次数。

    与真 BM25 同口径（`tokenize`），但离线用纯计数替身——长查询也能打分，
    而不是退化整句子串匹配。
    """
    tokens = tokenize(query)
    if not tokens:
        return 0.0
    lowered = text.lower()
    return float(sum(lowered.count(tok) for tok in tokens))


class DeterministicLexicalIndex:
    """实现 LexicalIndex Protocol：按查询 token 与 chunk 的重叠度打分。

    离线用计数替身（rank-bm25 是 `[rag]` extra，离线不要求安装）；
    `--real` 时换成真 Bm25Index。
    """

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    def add_chunks(self, chunks: list[Chunk]) -> None:
        self._chunks.extend(chunks)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        scored = [(s, c) for c in self._chunks if (s := _keyword_score(c.text, query)) > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [SearchHit(chunk=c, score=float(s)) for s, c in scored[:top_k]]


class DeterministicReranker:
    """实现 Reranker Protocol：按查询 token 重叠度精排（与词法索引同口径）。"""

    def rerank(
        self, query: str, hits: list[SearchHit], *, top_k: int
    ) -> list[SearchHit]:
        ranked = sorted(
            hits, key=lambda h: _keyword_score(h.chunk.text, query), reverse=True
        )
        return ranked[:top_k]


class KeywordQueryRewriter:
    """离线确定性改写：把查询改写成检索关键词（拉丁词 + CJK 双字重叠）。

    与线上 `LLMQueryRewriter` 同接口，但零依赖、确定性，只用于离线评测的
    rewrite 轴验证链路；真实 LLM 改写的收益用 `--real` 测量。
    """

    async def rewrite(self, query: str) -> str:
        return " ".join(tokenize(query))
