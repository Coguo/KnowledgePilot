"""Embedding 模型封装：本地 BGE-M3，重依赖懒加载。

- `Embedder` Protocol：同步 `embed(texts)`，调用方（Retriever / ingestion）用
  `asyncio.to_thread` 包裹，避免 CPU 推理阻塞事件循环。
- `BGEM3Embedder`：首次 `embed()` 才 import sentence-transformers 并加载模型，
  保证未启用 RAG 时应用不触碰 torch 等重依赖。
- `get_shared_embedder`：进程级单例，模型只加载一次，跨请求共享。
"""

from typing import Protocol

# sentence-transformers 仅用于类型标注/懒加载内部 import，模块顶层不引入。


class Embedder(Protocol):
    """Embedding 契约。"""

    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """文本列表 → 归一化向量列表。"""
        ...


class BGEM3Embedder:
    """本地 BGE-M3（sentence-transformers）。重依赖在首次 embed 时懒加载。"""

    dimensions = 1024  # BGE-M3 的固定输出维度

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        cache_dir: str = "",
        device: str = "cpu",
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._device = device
        self._model = None  # 首次 embed() 才加载

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer  # 懒加载重依赖

        kwargs = {"device": self._device}
        if self._cache_dir:
            kwargs["cache_folder"] = self._cache_dir
        self._model = SentenceTransformer(self._model_name, **kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,  # 归一化后内积即余弦相似度
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]


class _EmbedderConfig(Protocol):
    """get_shared_embedder 所需的配置子集（避免依赖 config 模块）。"""

    embedding_model: str
    embedding_cache_dir: str
    embedding_device: str


_shared: BGEM3Embedder | None = None


def get_shared_embedder(settings: _EmbedderConfig) -> BGEM3Embedder:
    """进程级单例：模型只加载一次。"""
    global _shared
    if _shared is None:
        _shared = BGEM3Embedder(
            model_name=settings.embedding_model,
            cache_dir=settings.embedding_cache_dir,
            device=settings.embedding_device,
        )
    return _shared
