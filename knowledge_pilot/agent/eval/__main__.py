"""Agent Evaluation CLI（规格 §13）。

用法：
    # 离线五档（确定性脚本化 LLM + rubric judge，零 key / 零重依赖；可无 langgraph 跑 loop 档）
    python -m knowledge_pilot.agent.eval --dataset tests/fixtures/eval_agent/small.json

    # 只跑某几档（'all' 是「graph+memory+kg+mcp」那一档的档名，不是哨兵）
    python -m knowledge_pilot.agent.eval --dataset ... --variants loop,graph+memory,all

    # 真实模式（需 .env 的 DEEPSEEK_API_KEY；联网费 key，每条多一次 judge complete）
    python -m knowledge_pilot.agent.eval --dataset ... --real

    # 结果落 JSON（供文档引用）
    python -m knowledge_pilot.agent.eval --dataset ... --json-out docs/agent-eval-results.json
"""

import argparse
import asyncio
import json
import sys

from knowledge_pilot.agent.eval.dataset import KNOWN_VARIANT_NAMES, load_dataset
from knowledge_pilot.agent.eval.runner import (
    format_results_table,
    run_agent_eval,
)


def _results_to_dict(results) -> list[dict]:
    """聚合行 → 可 JSON 化的 dict（延迟转毫秒，其它指标取整便于文档引用）。"""
    return [
        {
            "variant": r.variant,
            "n_items": r.n_items,
            "task_success": round(r.task_success, 4),
            "tool_selection": round(r.tool_selection, 4),
            "tool_argument": round(r.tool_argument, 4),
            "error_rate": round(r.error_rate, 4),
            "coverage": round(r.coverage, 4),
            "latency_p50_ms": round(r.latency_p50 * 1000, 2),
            "latency_p95_ms": round(r.latency_p95 * 1000, 2),
            "stream_calls_avg": round(r.stream_calls_avg, 2),
            "complete_calls_avg": round(r.complete_calls_avg, 2),
            "tokens_avg": round(r.tokens_avg, 1),
            "mem_found_avg": round(r.mem_found_avg, 3),
            "kg_triples_avg": round(r.kg_triples_avg, 3),
            "judge": r.judge,
        }
        for r in results
    ]


def _parse_variants(value: str) -> list[str] | None:
    """--variants 过滤：省略 → None（跑满五档）；给出 → 校验并返回列表。"""
    names = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [n for n in names if n not in KNOWN_VARIANT_NAMES]
    if unknown:
        raise ValueError(
            f"未知变体 {', '.join(unknown)!r}（合法：{', '.join(sorted(KNOWN_VARIANT_NAMES))}）"
        )
    return names


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="knowledge_pilot.agent.eval",
        description=(
            "Agent Evaluation：五档架构变体 × 数据集 → Task Success / 工具精度 / "
            "错误率 / 延迟 / token 成本"
        ),
    )
    parser.add_argument("--dataset", required=True, help="评测数据集 JSON 路径")
    parser.add_argument(
        "--variants",
        default="",
        help="逗号分隔的档名（loop,graph,graph+memory,graph+kg,all）；省略 = 五档全跑",
    )
    parser.add_argument("--max-iterations", type=int, default=3, help="graph 研究自评迭代上限")
    parser.add_argument("--no-warmup", action="store_true", help="跳过 graph 档首跑预热")
    parser.add_argument(
        "--real",
        action="store_true",
        help="真实模式：DeepSeek LLM judge + 真实搜索（需 .env 的 DEEPSEEK_API_KEY，联网费 key）",
    )
    parser.add_argument("--json-out", default="", help="把结果写成 JSON 文件")
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset)
    variants = _parse_variants(args.variants) or None

    if args.real:
        try:
            from knowledge_pilot.agent.eval.real import make_real_components

            components = make_real_components()
        except ValueError as exc:
            print(f"无法进入 --real 模式：{exc}", file=sys.stderr)
            return 2
        mode = "real（真实 DeepSeek LLM judge + 真实搜索，联网费 key）"
    else:
        from knowledge_pilot.agent.eval.offline import make_offline_components

        components = make_offline_components()
        mode = "offline（确定性脚本化 LLM + rubric judge，零 key 可复现）"

    results = await run_agent_eval(
        dataset,
        components=components,
        variants=variants,
        max_iterations=args.max_iterations,
        warmup=not args.no_warmup,
    )

    print(
        f"数据集: {args.dataset}（{len(dataset.items)} 条 query）  "
        f"max_iterations={args.max_iterations}  模式: {mode}"
    )
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
