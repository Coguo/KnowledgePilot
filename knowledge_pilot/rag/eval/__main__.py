"""评测 CLI。

用法：
    # 离线矩阵（确定性，零重依赖）
    python -m knowledge_pilot.rag.eval --dataset tests/fixtures/eval/small.json

    # 只跑某几个轴组合
    python -m knowledge_pilot.rag.eval --dataset ... --retrieval hybrid --rerank rerank

    # 真实组件（需 [rag] + 首次模型下载；国内可用 HF_ENDPOINT=https://hf-mirror.com）
    python -m knowledge_pilot.rag.eval --dataset ... --real

    # 结果落 JSON（供文档引用）
    python -m knowledge_pilot.rag.eval --dataset ... --json-out docs/eval-results.json
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from knowledge_pilot.rag.eval.dataset import load_dataset
from knowledge_pilot.rag.eval.runner import Spec, format_results_table, run_eval


def _filter_set(value: str) -> set[str] | None:
    """解析轴过滤：'all' → 不过滤；否则按逗号拆成集合。"""
    if value == "all":
        return None
    return set(value.split(","))


def _build_filter(args: argparse.Namespace) -> Any:
    chunk = _filter_set(args.chunk)
    retrieval = _filter_set(args.retrieval)
    rerank = _filter_set(args.rerank)
    rewrite = _filter_set(args.rewrite)

    def spec_filter(spec: Spec) -> bool:
        return (
            (chunk is None or spec.chunk in chunk)
            and (retrieval is None or spec.retrieval in retrieval)
            and (rerank is None or spec.rerank in rerank)
            and (rewrite is None or spec.rewrite in rewrite)
        )

    return spec_filter


def _results_to_dict(results: list) -> list[dict]:
    return [
        {
            "chunk": r.spec.chunk,
            "retrieval": r.spec.retrieval,
            "rerank": r.spec.rerank,
            "rewrite": r.spec.rewrite,
            "recall": round(r.recall, 4),
            "mrr": round(r.mrr, 4),
            "latency_p50_ms": round(r.latency_p50 * 1000, 2),
            "latency_p95_ms": round(r.latency_p95 * 1000, 2),
            "token_cost": round(r.token_cost, 1),
        }
        for r in results
    ]


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="knowledge_pilot.rag.eval",
        description="RAG 检索侧评测：16 行 Spec 矩阵 × 数据集 → Recall@K/MRR/Latency/Token Cost",
    )
    parser.add_argument("--dataset", required=True, help="评测数据集 JSON 路径")
    parser.add_argument("--top-k", type=int, default=3, help="最终返回条数（Recall@K 的 K）")
    parser.add_argument("--rerank-candidates", type=int, default=20, help="精排候选池大小")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument("--chunk", default="all", help="fixed|recursive|all 或逗号组合")
    parser.add_argument("--retrieval", default="all", help="vector|hybrid|all 或逗号组合")
    parser.add_argument("--rerank", default="all", help="no|rerank|all 或逗号组合")
    parser.add_argument("--rewrite", default="all", help="original|rewritten|all 或逗号组合")
    parser.add_argument(
        "--real", action="store_true", help="用真实 embedder/BM25/reranker/LLM 改写"
    )
    parser.add_argument("--json-out", default="", help="把结果写成 JSON 文件")
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset)

    if args.real:
        from knowledge_pilot.rag.eval.real import make_real_components

        comp = make_real_components()
        mode = "real（真实模型，首次运行会下载模型）"
    else:
        from knowledge_pilot.rag.eval.runner import make_offline_components

        comp = make_offline_components()
        mode = "offline（确定性组件）"

    results = await run_eval(
        dataset,
        comp=comp,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        top_k=args.top_k,
        rerank_candidates=args.rerank_candidates,
        spec_filter=_build_filter(args),
    )

    print(f"数据集: {args.dataset}（{len(dataset.items)} 条 query）  top_k={args.top_k}  模式: {mode}")
    print()
    print(format_results_table(results))
    print()

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(_results_to_dict(results), fh, ensure_ascii=False, indent=2)
        print(f"结果已写入: {args.json_out}")
    return 0


def _fix_stdout() -> None:
    """Windows 管道下 stdout 可能回退到 GBK：统一 UTF-8 输出，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8")
        except Exception:
            pass  # 重配失败不影响主流程


def main() -> int:
    _fix_stdout()
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
