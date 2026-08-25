"""Retriever：相似度排序 / top_k / format_context 带来源。"""

from knowledge_pilot.rag.documents import Chunk
from knowledge_pilot.rag.retriever import Retriever

from tests.fakes import FakeEmbedder, InMemoryVectorStore

MATCH_TEXT = "检索目标文本"


async def _prepared():
    store = InMemoryVectorStore()
    chunks = [
        Chunk(
            document_id="d", chunk_id="d:0", source="web",
            url="https://a.example", title="A", text=MATCH_TEXT,
        ),
        Chunk(
            document_id="d", chunk_id="d:1", source="web",
            url="https://b.example", title="B", text="完全无关的别的内容",
        ),
    ]
    embedder = FakeEmbedder()
    vectors = embedder.embed([c.text for c in chunks])
    await store.add_chunks(chunks, vectors)
    return store, embedder


async def test_retrieve_ranks_by_similarity():
    store, embedder = await _prepared()
    retriever = Retriever(embedder, store, top_k=1)

    hits = await retriever.retrieve(MATCH_TEXT)

    assert len(hits) == 1
    assert hits[0].chunk.url == "https://a.example"


async def test_format_context_contains_source():
    store, embedder = await _prepared()
    retriever = Retriever(embedder, store, top_k=2)

    hits = await retriever.retrieve(MATCH_TEXT)
    context = retriever.format_context(hits)

    assert "[来源:" in context
    assert "https://a.example" in context
    assert MATCH_TEXT in context


async def test_format_context_truncates_long_snippets():
    store, embedder = await _prepared()
    retriever = Retriever(embedder, store, top_k=1)

    hits = await retriever.retrieve(MATCH_TEXT)
    context = retriever.format_context(hits, snippet_len=5)

    body = context.split("\n")[-1]  # 来源行后的正文行
    assert len(body) <= 5
