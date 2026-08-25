"""RAG 模块：文档获取 → 分块 → Embedding → 向量库 → 检索。

对外只暴露 `create_rag_pipeline(settings)` 工厂。重依赖（sentence-transformers /
chromadb / rank_bm25）全部懒加载：未启用 RAG 或未安装 `[rag]` extra 时，
应用照常启动。
"""

from uuid import uuid4

from knowledge_pilot.config import Settings
from knowledge_pilot.llm.client import LLMClient
from knowledge_pilot.rag.pipeline import RAGPipeline


def create_rag_pipeline(
    settings: Settings, llm: LLMClient | None = None
) -> RAGPipeline | None:
    """按配置构建 RAG 流水线；`rag_enabled` 为假返回 None。

    `llm` 可选：查询改写（Query Rewrite）开启时用它做改写调用。
    未安装 `[rag]` extra 时抛 ImportError（由 api 层转成清晰 HTTPException）。
    """
    if not settings.rag_enabled:
        return None

    # 局部 import：让重依赖的 import 尽量推迟到真正需要时。
    from knowledge_pilot.rag.chunker import create_chunker
    from knowledge_pilot.rag.embedder import get_shared_embedder
    from knowledge_pilot.rag.fetcher import PageFetcher
    from knowledge_pilot.rag.hybrid import HybridRetriever
    from knowledge_pilot.rag.lexical import Bm25Index
    from knowledge_pilot.rag.reranker import get_shared_reranker
    from knowledge_pilot.rag.retriever import Retriever
    from knowledge_pilot.rag.rewrite import LLMQueryRewriter
    from knowledge_pilot.rag.store import ChromaStore

    embedder = get_shared_embedder(settings)  # 便宜：不真正加载模型
    store = ChromaStore(
        persist_dir=settings.chroma_dir,
        collection_name=f"task_{uuid4().hex}",  # 每研究任务独立临时知识库
    )
    chunker = create_chunker(
        settings.rag_chunk_strategy,
        settings.rag_chunk_size,
        settings.rag_chunk_overlap,
    )
    fetcher = PageFetcher(timeout=settings.rag_fetch_timeout)

    vector = Retriever(embedder=embedder, store=store, top_k=settings.rag_top_k)
    lexical = Bm25Index() if settings.rag_hybrid_enabled else None
    retriever = (
        HybridRetriever(
            vector,
            lexical,
            top_k=settings.rag_rerank_candidates,
            branch_top_k=settings.rag_rerank_candidates,
        )
        if settings.rag_hybrid_enabled
        else vector
    )
    reranker = get_shared_reranker(settings) if settings.rag_rerank_enabled else None
    rewriter = (
        LLMQueryRewriter(llm)
        if (settings.rag_query_rewrite_enabled and llm is not None)
        else None
    )

    return RAGPipeline(
        fetcher=fetcher,
        chunker=chunker,
        embedder=embedder,
        store=store,
        retriever=retriever,
        lexical=lexical,
        reranker=reranker,
        rewriter=rewriter,
        top_k=settings.rag_top_k,
        rerank_candidates=settings.rag_rerank_candidates,
        max_fetch_urls=settings.rag_max_fetch_urls,
    )
