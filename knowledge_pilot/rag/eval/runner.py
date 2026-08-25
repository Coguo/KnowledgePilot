"""评测运行器：Spec 矩阵 × 数据集 → 每 Spec 的聚合指标。

每条检索链路对应线上 `pipeline.enrich_search` 的检索段（去掉抓取）：
    改写(可选) → 向量/混合检索候选池 → 精排(可选) → top_k → 指标。
每个 (spec, item) 独立建库（新 store + 新词法索引），确保不串数据。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

from knowledge_pilot.rag.chunker import create_chunker
from knowledge_pilot.rag.documents import Document
from knowledge_pilot.rag.embedder import Embedder
from knowledge_pilot.rag.eval.dataset import EvalDataset, EvalItem
from knowledge_pilot.rag.eval.metrics import (
    est_tokens,
    latency_stats,
    mrr,
    recall_at_k,
)
from knowledge_pilot.rag.eval.offline import (
    DeterministicEmbedder,
    DeterministicLexicalIndex,
    DeterministicReranker,
    InMemoryStore,
    KeywordQueryRewriter,
)
from knowledge_pilot.rag.hybrid import HybridRetriever
from knowledge_pilot.rag.ingestion import ingest_documents
from knowledge_pilot.rag.lexical import LexicalIndex
from knowledge_pilot.rag.reranker import Reranker
from knowledge_pilot.rag.retriever import Retriever, format_hits_context
from knowledge_pilot.rag.rewrite import QueryRewriter
from knowledge_pilot.rag.store import VectorStore

_CHUNK_STRATEGIES = ("fixed", "recursive")
_RETRIEVALS = ("vector", "hybrid")
_RERANK_CHOICES = ("no", "rerank")
_REWRITE_CHOICES = ("original", "rewritten")


@dataclass(frozen=True)
class Spec:
    """检索链路的一个配置点（四个优化轴的取值组合）。"""

    chunk: str  # fixed / recursive
    retrieval: str  # vector / hybrid
    rerank: str  # no / rerank
    rewrite: str  # original / rewritten


def all_specs() -> list[Spec]:
    """16 行全矩阵：{fixed,recursive}×{vector,hybrid}×{no,rerank}×{original,rewritten}。"""
    return [
        Spec(c, r, rk, rw)
        for c in _CHUNK_STRATEGIES
        for r in _RETRIEVALS
        for rk in _RERANK_CHOICES
        for rw in _REWRITE_CHOICES
    ]


@dataclass
class EvalComponents:
    """评测组件包：离线确定性实现与 --real 真实实现互换。

    `lexical_factory` 是零参工厂而非实例：词法索引会累积语料，必须
    **每个 item 独立建一个**，否则跨 (spec, item) 串数据、结果不可复现。
    `reranker` 仅在 spec.rerank == "rerank" 时使用；`rewriter_llm` 仅在
    spec.rewrite == "rewritten" 时使用。
    """

    embedder: Embedder
    lexical_factory: Callable[[], LexicalIndex]
    reranker: Reranker | None
    rewriter_llm: QueryRewriter


def make_offline_components() -> EvalComponents:
    """离线（默认）：确定性组件，零重依赖。"""
    return EvalComponents(
        embedder=DeterministicEmbedder(),
        lexical_factory=DeterministicLexicalIndex,
        reranker=DeterministicReranker(),
        rewriter_llm=KeywordQueryRewriter(),
    )


@dataclass(frozen=True)
class SpecResult:
    """一个 Spec 在整份数据集上的聚合结果。"""

    spec: Spec
    recall: float  # 平均 Recall@K
    mrr: float  # 平均 MRR
    latency_p50: float  # 秒
    latency_p95: float  # 秒
    token_cost: float  # 平均估算 token（改写 query + 检索上下文）


@dataclass(frozen=True)
class _ItemResult:
    recall: float
    mrr: float
    latency: float
    token_cost: float


async def _run_item(
    spec: Spec,
    item: EvalItem,
    comp: EvalComponents,
    *,
    chunk_size: int,
    chunk_overlap: int,
    top_k: int,
    rerank_candidates: int,
) -> _ItemResult:
    chunker = create_chunker(spec.chunk, chunk_size, chunk_overlap)
    store: VectorStore = InMemoryStore()
    lexical = comp.lexical_factory()  # 每 item 独立词法库，杜绝跨 item 串语料

    docs = [
        Document(
            document_id=d.document_id,
            source="eval",
            url=d.url,
            title=d.title,
            text=d.text,
        )
        for d in item.docs
    ]
    await ingest_documents(
        docs,
        chunker=chunker,
        embedder=comp.embedder,
        store=store,
        lexical=lexical if spec.retrieval == "hybrid" else None,
    )

    query = (
        await comp.rewriter_llm.rewrite(item.query)
        if spec.rewrite == "rewritten"
        else item.query
    )

    vector = Retriever(embedder=comp.embedder, store=store, top_k=top_k)
    if spec.retrieval == "hybrid":
        retriever = HybridRetriever(
            vector,
            lexical,
            top_k=rerank_candidates,
            branch_top_k=rerank_candidates,
        )
    else:
        retriever = vector

    # 只计时检索段（精排/融合），不含建库——评测关注线上检索的延迟成本。
    start = time.perf_counter()
    hits = await retriever.retrieve(query, top_k=rerank_candidates)
    if spec.rerank == "rerank" and comp.reranker is not None:
        hits = await asyncio.to_thread(comp.reranker.rerank, query, hits, top_k=top_k)
    else:
        hits = hits[:top_k]
    latency = time.perf_counter() - start

    # document 级去重：同一文档多个 chunk 只算一次，保留排序。
    hit_docs: list[str] = []
    seen: set[str] = set()
    for h in hits:
        if h.chunk.document_id not in seen:
            seen.add(h.chunk.document_id)
            hit_docs.append(h.chunk.document_id)

    context = "\n\n【知识库检索结果】\n" + format_hits_context(hits)
    token_cost = est_tokens(query) + est_tokens(context)

    return _ItemResult(
        recall=recall_at_k(hit_docs, item.relevant_doc_ids, top_k),
        mrr=mrr(hit_docs, item.relevant_doc_ids),
        latency=latency,
        token_cost=float(token_cost),
    )


async def _run_spec(
    spec: Spec,
    dataset: EvalDataset,
    comp: EvalComponents,
    *,
    chunk_size: int,
    chunk_overlap: int,
    top_k: int,
    rerank_candidates: int,
) -> SpecResult:
    item_results = [
        await _run_item(
            spec,
            item,
            comp,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            top_k=top_k,
            rerank_candidates=rerank_candidates,
        )
        for item in dataset.items
    ]
    stats = latency_stats([r.latency for r in item_results])
    n = len(item_results)
    return SpecResult(
        spec=spec,
        recall=sum(r.recall for r in item_results) / n,
        mrr=sum(r.mrr for r in item_results) / n,
        latency_p50=stats["p50"],
        latency_p95=stats["p95"],
        token_cost=sum(r.token_cost for r in item_results) / n,
    )


async def run_eval(
    dataset: EvalDataset,
    *,
    comp: EvalComponents,
    chunk_size: int = 800,
    chunk_overlap: int = 200,
    top_k: int = 3,
    rerank_candidates: int = 20,
    spec_filter: Callable[[Spec], bool] | None = None,
) -> list[SpecResult]:
    """跑完整 Spec 矩阵（或 `spec_filter` 过滤后的子集），返回每 Spec 聚合结果。"""
    specs = [s for s in all_specs() if spec_filter is None or spec_filter(s)]
    return [
        await _run_spec(
            spec,
            dataset,
            comp,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            top_k=top_k,
            rerank_candidates=rerank_candidates,
        )
        for spec in specs
    ]


def format_results_table(results: list[SpecResult]) -> str:
    """手写对齐表格输出（不引入 tabulate 依赖），供 CLI 与文档引用。"""
    headers = [
        "chunk",
        "retrieval",
        "rerank",
        "rewrite",
        "recall",
        "mrr",
        "lat_p50(ms)",
        "lat_p95(ms)",
        "tokens",
    ]
    rows = [
        [
            r.spec.chunk,
            r.spec.retrieval,
            r.spec.rerank,
            r.spec.rewrite,
            f"{r.recall:.3f}",
            f"{r.mrr:.3f}",
            f"{r.latency_p50 * 1000:.1f}",
            f"{r.latency_p95 * 1000:.1f}",
            f"{r.token_cost:.1f}",
        ]
        for r in sorted(
            results, key=lambda r: (r.spec.chunk, r.spec.retrieval, r.spec.rerank, r.spec.rewrite)
        )
    ]
    widths = [
        max(len(headers[i]), max((len(row[i]) for row in rows), default=0))
        for i in range(len(headers))
    ]
    sep = "  ".join("-" * w for w in widths)
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), sep]
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)
