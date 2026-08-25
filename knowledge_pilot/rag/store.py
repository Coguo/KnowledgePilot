"""向量库封装：Chroma（本地嵌入式、文件持久化、支持元数据过滤）。

- `VectorStore` Protocol：测试用 InMemoryVectorStore 注入，离线跑。
- `ChromaStore`：persist_dir 空 → EphemeralClient（内存、不写盘，适合测试/临时），
  非空 → PersistentClient。collection 用 cosine 空间，禁用匿名遥测。
- chromadb 是同步 API，统一用 `asyncio.to_thread` 包裹，避免阻塞事件循环。
- Chroma 的 metadata 只能存标量，正文放 documents，查询后重建 Chunk。
"""

import asyncio
from typing import Protocol
from uuid import uuid4

from knowledge_pilot.rag.documents import Chunk


class SearchHit:
    """一次检索命中。"""

    __slots__ = ("chunk", "score")

    def __init__(self, chunk: Chunk, score: float) -> None:
        self.chunk = chunk
        self.score = score


class VectorStore(Protocol):
    """向量库契约。"""

    async def add_chunks(
        self, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None: ...

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        where: dict | None = None,
    ) -> list[SearchHit]: ...

    async def count(self) -> int: ...


class ChromaStore:
    """Chroma 实现。chromadb 在 __init__ 内懒加载。"""

    def __init__(
        self,
        persist_dir: str = "",
        collection_name: str | None = None,
    ) -> None:
        import chromadb  # 懒加载重依赖
        from chromadb.config import Settings as ChromaSettings

        name = collection_name or f"task_{uuid4().hex}"
        chroma_settings = ChromaSettings(anonymized_telemetry=False)
        if persist_dir:
            self._client = chromadb.PersistentClient(
                path=persist_dir, settings=chroma_settings
            )
        else:
            self._client = chromadb.EphemeralClient(settings=chroma_settings)
        self._collection = self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    async def add_chunks(
        self, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        await asyncio.to_thread(self._add_sync, chunks, embeddings)

    def _add_sync(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        self._collection.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=embeddings,
            metadatas=[
                {
                    # 文档级元数据（search_score 等）一并持久化，检索后可还原；
                    # 显式键放最后，覆盖任何同名冲突。
                    **{
                        k: v
                        for k, v in c.metadata.items()
                        if isinstance(v, (str, int, float, bool))
                    },
                    "document_id": c.document_id,
                    "source": c.source,
                    "url": c.url,
                    "title": c.title,
                    "chunk_index": c.metadata.get("chunk_index", ""),
                    "chunk_total": c.metadata.get("chunk_total", ""),
                }
                for c in chunks
            ],
        )

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        where: dict | None = None,
    ) -> list[SearchHit]:
        if await self.count() == 0:
            return []
        return await asyncio.to_thread(
            self._search_sync, query_embedding, top_k, where
        )

    def _search_sync(
        self,
        query_embedding: list[float],
        top_k: int,
        where: dict | None,
    ) -> list[SearchHit]:
        res = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )
        ids = res.get("ids") or [[]]
        if not ids[0]:
            return []
        hits: list[SearchHit] = []
        for i, chunk_id in enumerate(ids[0]):
            meta = (res.get("metadatas") or [[{}]])[0][i] or {}
            hits.append(
                SearchHit(
                    chunk=Chunk(
                        document_id=meta.get("document_id", ""),
                        chunk_id=chunk_id,
                        source=meta.get("source", ""),
                        url=meta.get("url", ""),
                        title=meta.get("title", ""),
                        text=(res.get("documents") or [[]])[0][i] or "",
                        # 还原全部持久化元数据（search_score / chunk_index / chunk_total）
                        metadata={
                            k: v
                            for k, v in meta.items()
                            if k not in {"document_id", "source", "url", "title"}
                        },
                    ),
                    score=1.0 - (res.get("distances") or [[0.0]])[0][i],
                )
            )
        return hits

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)

    def delete_collection(self) -> None:
        """删除当前 collection（幂等）：用于任务结束清理临时知识库。

        同步调用即可（清理发生在请求收尾，不阻塞关键路径）。
        """
        try:
            self._client.delete_collection(name=self._collection.name)
        except Exception:
            # collection 可能已被删除（幂等清理，不因删除失败而报错）
            pass
