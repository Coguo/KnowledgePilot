"""RAG 模块：文档获取 → 分块 → Embedding → 向量库 → 检索。

对外只暴露 `create_rag_pipeline(settings)` 工厂。重依赖（sentence-transformers /
chromadb）全部懒加载：未启用 RAG 或未安装 `[rag]` extra 时，应用照常启动。
"""

from uuid import uuid4

from knowledge_pilot.config import Settings
from knowledge_pilot.rag.pipeline import RAGPipeline


def create_rag_pipeline(settings: Settings) -> RAGPipeline | None:
    """按配置构建 RAG 流水线；`rag_enabled` 为假返回 None。

    未安装 `[rag]` extra 时抛 ImportError（由 api 层转成清晰 HTTPException）。
    """
    if not settings.rag_enabled:
        return None

    # 局部 import：让重依赖的 import 尽量推迟到真正需要时。
    from knowledge_pilot.rag.chunker import FixedSizeChunker
    from knowledge_pilot.rag.embedder import get_shared_embedder
    from knowledge_pilot.rag.fetcher import PageFetcher
    from knowledge_pilot.rag.store import ChromaStore

    embedder = get_shared_embedder(settings)  # 便宜：不真正加载模型
    store = ChromaStore(
        persist_dir=settings.chroma_dir,
        collection_name=f"task_{uuid4().hex}",  # 每研究任务独立临时知识库
    )
    chunker = FixedSizeChunker(settings.rag_chunk_size, settings.rag_chunk_overlap)
    fetcher = PageFetcher(timeout=settings.rag_fetch_timeout)
    return RAGPipeline(
        fetcher=fetcher,
        chunker=chunker,
        embedder=embedder,
        store=store,
        top_k=settings.rag_top_k,
        max_fetch_urls=settings.rag_max_fetch_urls,
    )
