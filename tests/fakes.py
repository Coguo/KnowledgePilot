"""测试共享的 Fake：脚本化 LLM / Embedding / 向量库 / 抓取器，全程不联网。"""

import math
import random

from knowledge_pilot.llm.client import StreamChunk
from knowledge_pilot.rag.documents import Chunk
from knowledge_pilot.rag.fetcher import FetchedDocument
from knowledge_pilot.rag.store import SearchHit


class FakeChatClient:
    """脚本化 LLM：按轮次返回预设内容 / 工具调用。

    script: list of (content_parts: list[str], tool_calls: list[dict])
    超过轮次后重复最后一个条目（用于测试循环兜底）。
    """

    model = "fake"

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list[list[dict]] = []

    async def stream_chat(self, messages, tools=None):
        # 存副本：调用方后续还会往同一个 list 追加消息，不能存引用。
        self.seen_messages.append(list(messages))
        self.seen_tools.append(tools)

        content_parts, tool_calls = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        for part in content_parts:
            yield StreamChunk(content_delta=part)

        for index, tc in enumerate(tool_calls):
            # 名称一次到位、参数分两次到达，专门验证增量累加逻辑。
            yield StreamChunk(tool_call_delta={
                "index": index, "id": f"call_{index}", "name": tc["name"], "arguments": None,
            })
            mid = len(tc["arguments"]) // 2
            yield StreamChunk(tool_call_delta={
                "index": index, "id": None, "name": None, "arguments": tc["arguments"][:mid],
            })
            yield StreamChunk(tool_call_delta={
                "index": index, "id": None, "name": None, "arguments": tc["arguments"][mid:],
            })


class FakeEmbedder:
    """确定性 Embedding：相同文本 → 相同归一化向量，用于离线检索测试。"""

    def __init__(self, dimensions: int = 8):
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out: list[list[float]] = []
        for text in texts:
            rng = random.Random(text)  # 由文本哈希播种：相同文本 → 相同向量
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


class InMemoryVectorStore:
    """实现 VectorStore Protocol：余弦相似度检索，不依赖 chromadb。"""

    def __init__(self):
        self._items: list[tuple[Chunk, list[float]]] = []

    async def add_chunks(
        self, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        for chunk, embedding in zip(chunks, embeddings):
            self._items.append((chunk, embedding))

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        where: dict | None = None,
    ) -> list[SearchHit]:
        scored: list[tuple[float, Chunk]] = []
        for chunk, embedding in self._items:
            if where and not all(
                chunk.metadata.get(k) == v for k, v in where.items()
            ):
                continue
            scored.append((_cosine(query_embedding, embedding), chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [SearchHit(chunk=c, score=s) for s, c in scored[:top_k]]

    async def count(self) -> int:
        return len(self._items)

    def all_chunks(self) -> list[Chunk]:
        return [chunk for chunk, _ in self._items]


class StubFetcher:
    """可编程抓取器：给定 url → FetchedDocument 映射，可指定失败 URL。"""

    def __init__(
        self,
        docs: dict[str, FetchedDocument],
        fail_urls: set[str] | None = None,
    ) -> None:
        self._docs = docs
        self._fail_urls = set(fail_urls or [])
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> FetchedDocument | None:
        if url in self._fail_urls:
            return None
        self.fetched.append(url)
        return self._docs.get(url)

    async def fetch_many(
        self, urls: list[str], *, concurrency: int = 3
    ) -> list[FetchedDocument]:
        out: list[FetchedDocument] = []
        for url in urls:
            doc = await self.fetch(url)
            if doc is not None:
                out.append(doc)
        return out
