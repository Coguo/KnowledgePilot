"""重排序器：对粗排候选做二次精排（CrossEncoder），提升 top-k 相关性。

向量/混合检索的粗排是双塔近似相似；bge-reranker 是 CrossEncoder——
把 query 与每个候选拼成对做联合编码，相关性判断更准。代价是每个候选
都要前向一次，所以只对 top-N 候选精排（候选数由配置旋钮控制）。
"""

from typing import Protocol

from knowledge_pilot.rag.store import SearchHit


class Reranker(Protocol):
    """重排序器契约。同步接口：CPU 密集，调用方用 asyncio.to_thread 包裹。"""

    def rerank(
        self, query: str, hits: list[SearchHit], *, top_k: int
    ) -> list[SearchHit]: ...


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder（BAAI/bge-reranker-base）实现。

    懒加载：首次 rerank 才 import 并加载模型（约 1.1GB，可用 HF 镜像下载）。
    输出分数只用于排序，不做阈值判断（CrossEncoder 分数未校准）。
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        cache_dir: str = "",
        device: str = "cpu",
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._device = device
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import CrossEncoder  # 懒加载重依赖

        try:
            self._model = CrossEncoder(
                self._model_name,
                device=self._device,
                cache_folder=self._cache_dir or None,
            )
        except TypeError:
            # 旧版 CrossEncoder 不支持 cache_folder：退回环境 HF 默认缓存
            self._model = CrossEncoder(self._model_name, device=self._device)

    def rerank(
        self, query: str, hits: list[SearchHit], *, top_k: int
    ) -> list[SearchHit]:
        if not hits:
            return []
        self._ensure_loaded()
        pairs = [(query, h.chunk.text) for h in hits]
        scores = self._model.predict(pairs)
        ranked = sorted(zip(hits, scores), key=lambda x: x[1], reverse=True)
        return [SearchHit(chunk=h.chunk, score=float(s)) for h, s in ranked[:top_k]]


class _RerankerConfig(Protocol):
    """get_shared_reranker 的配置契约：避免 import config 模块。"""

    rag_rerank_model: str
    embedding_cache_dir: str
    embedding_device: str


_shared: CrossEncoderReranker | None = None


def get_shared_reranker(settings: _RerankerConfig) -> CrossEncoderReranker:
    """进程级单例：同进程只加载一次 rerank 模型（与 embedder 一致）。"""
    global _shared
    if _shared is None:
        _shared = CrossEncoderReranker(
            model_name=settings.rag_rerank_model,
            cache_dir=settings.embedding_cache_dir,
            device=settings.embedding_device,
        )
    return _shared
