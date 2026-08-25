"""真实组件：`--real` 模式用真实 embedder / BM25 / reranker / LLM 改写。

重依赖（sentence-transformers / rank-bm25）在各自组件的首次使用时才真正
加载；本模块只在 CLI 显式传 `--real` 时被 import。
"""

from knowledge_pilot.config import settings
from knowledge_pilot.llm.client import ChatClient
from knowledge_pilot.rag.embedder import get_shared_embedder
from knowledge_pilot.rag.eval.runner import EvalComponents
from knowledge_pilot.rag.lexical import Bm25Index
from knowledge_pilot.rag.reranker import get_shared_reranker
from knowledge_pilot.rag.rewrite import LLMQueryRewriter


def make_real_components() -> EvalComponents:
    """用配置装配真实组件；首次使用触发模型下载（BGE-M3 ~2GB / bge-reranker ~1.1GB）。"""
    return EvalComponents(
        embedder=get_shared_embedder(settings),
        lexical_factory=Bm25Index,
        reranker=get_shared_reranker(settings),
        rewriter_llm=LLMQueryRewriter(ChatClient(settings)),
    )
