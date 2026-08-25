"""文档入库流水线：抓取结果 → Document → Chunk → Embedding → 向量库。

`ingest_from_results` 输入 `list[SearchResult]`（搜索抽象层的产物），
即预留了"用户提供 URL"的扩展点：未来只需把用户 URL 组装成 Document 再调
`ingest_documents`，流水线不变。
"""

import asyncio
from dataclasses import dataclass

from knowledge_pilot.rag.chunker import Chunker
from knowledge_pilot.rag.documents import Document, make_document_id
from knowledge_pilot.rag.embedder import Embedder
from knowledge_pilot.rag.fetcher import PageFetcher
from knowledge_pilot.rag.lexical import LexicalIndex
from knowledge_pilot.rag.store import VectorStore
from knowledge_pilot.search.base import SearchResult


@dataclass
class IngestReport:
    documents: int
    chunks: int
    skipped: int


async def ingest_documents(
    docs: list[Document],
    *,
    chunker: Chunker,
    embedder: Embedder,
    store: VectorStore,
    lexical: LexicalIndex | None = None,
) -> IngestReport:
    """对文档分块 → 一次性批量 embedding → 入向量库。

    `lexical` 可选：传入时同一批 chunk 也喂入词法索引（BM25），供混合检索。
    """
    chunks: list = []
    accepted = 0
    skipped = 0
    for doc in docs:
        doc_chunks = chunker.chunk(doc)
        if doc_chunks:
            chunks.extend(doc_chunks)
            accepted += 1
        else:
            skipped += 1

    if not chunks:
        return IngestReport(documents=0, chunks=0, skipped=skipped)

    if lexical is not None:
        lexical.add_chunks(chunks)
    embeddings = await asyncio.to_thread(embedder.embed, [c.text for c in chunks])
    await store.add_chunks(chunks, embeddings)
    return IngestReport(documents=accepted, chunks=len(chunks), skipped=skipped)


async def ingest_from_results(
    results: list[SearchResult],
    *,
    fetcher: PageFetcher,
    chunker: Chunker,
    embedder: Embedder,
    store: VectorStore,
    lexical: LexicalIndex | None = None,
    max_urls: int = 3,
    min_text_len: int = 100,
) -> IngestReport:
    """抓取搜索结果的 top-N 网页正文并建库；失败回退摘要，太短跳过。"""
    target = results[:max_urls]
    fetched = await fetcher.fetch_many([r.url for r in target])
    fetched_by_url = {f.url: f for f in fetched}

    docs: list[Document] = []
    skipped = 0
    for result in target:
        fetched_doc = fetched_by_url.get(result.url)
        text = fetched_doc.text if fetched_doc is not None else result.content
        if not text or len(text.strip()) < min_text_len:
            skipped += 1
            continue
        docs.append(
            Document(
                document_id=make_document_id(result.url),
                source="web",
                url=result.url,
                title=fetched_doc.title if fetched_doc is not None else result.title,
                text=text.strip(),
                metadata=(
                    {"search_score": result.metadata.get("score", "")}
                    if result.metadata
                    else {}
                ),
            )
        )

    report = await ingest_documents(
        docs, chunker=chunker, embedder=embedder, store=store, lexical=lexical
    )
    report.skipped += skipped
    return report
