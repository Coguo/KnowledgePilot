"""RAG 检索侧离线评测：数据集 + 指标 + 运行器 + CLI。

回答规格 §6 的问题"为什么新方案比旧方案更好"——用 Recall@K / MRR /
Latency / Token Cost 四类指标，在 {fixed, recursive} × {vector, hybrid} ×
{no, rerank} × {original, rewritten} 的 16 行矩阵上量化每个优化轴的收益。

- `offline`（默认）：确定性组件，零重依赖，装好 dev 依赖即可跑。
- `--real`：真实 embedder / BM25 / reranker / LLM 改写，需 `[rag]` + 模型下载。

CLI 用法：
    python -m knowledge_pilot.rag.eval --dataset tests/fixtures/eval/small.json
    python -m knowledge_pilot.rag.eval --dataset ... --chunk recursive --real
"""

from knowledge_pilot.rag.eval.dataset import EvalDataset, EvalDoc, EvalItem, load_dataset
from knowledge_pilot.rag.eval.metrics import est_tokens, latency_stats, mrr, recall_at_k
from knowledge_pilot.rag.eval.runner import (
    EvalComponents,
    Spec,
    SpecResult,
    all_specs,
    format_results_table,
    run_eval,
)

__all__ = [
    "EvalDataset",
    "EvalDoc",
    "EvalItem",
    "load_dataset",
    "est_tokens",
    "latency_stats",
    "mrr",
    "recall_at_k",
    "EvalComponents",
    "Spec",
    "SpecResult",
    "all_specs",
    "format_results_table",
    "run_eval",
]
