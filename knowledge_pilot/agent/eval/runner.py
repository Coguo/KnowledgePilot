"""Agent Evaluation 运行器：五档变体 × 数据集 → 每档聚合指标 + 表格。

镜像 `rag/eval/runner` 的分层：`components`（离线确定性 / --real）提供 make_judge /
make_context(item, variant) / make_warmup_context(variant) 三件套，本模块只按
变体配置驱动 loop/graph 并聚合，不关心具体实现。每个 (item, variant) 独立组件
（离线每档现建脚本化 LLM + 记忆库；--real 用独立 CountingChatClient）→ 结果隔离可复现。

事件重建 transcript：最终报告 = 最后一个 DoneEvent.content；工具调用 = ToolCallEvent
(name, arguments JSON)；无错误哨兵——**跑通才算 ok**：抛异常或没产出 Done 都记 error
（error_kind），该条 task_success=0、coverage=0（错误/空跑无报告可判）。未知工具
ValueError 会一路冒泡出整次 run（loop/graph 均无 try/except，已核验）——这是
mcp_only_tool 条目在非 mcp 档的预期行为，落进 error_rate。

graph driver（run_research_graph）只在真正驱动时才懒导入 → 本模块在无 langgraph
环境可 import，loop 变体可离线跑（本地冒烟依赖这一点）；B 轨完整矩阵需 langgraph。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from knowledge_pilot.agent.eval.dataset import AgentDataset, AgentItem
from knowledge_pilot.agent.eval.metrics import (
    coverage,
    latency_stats,
    mean,
    tool_argument_accuracy,
    tool_selection_accuracy,
)
from knowledge_pilot.agent.eval.offline import ALL_VARIANTS, Variant
from knowledge_pilot.agent.events import DoneEvent, KgEvent, MemoryEvent, ToolCallEvent


@dataclass
class ItemOutcome:
    """一条 (item, variant) 的明细结果（单档聚合的数据来源，表格不直接展示）。"""

    query: str
    ok: bool  # 完整跑通（无异常且收到 Done）
    error_kind: str | None
    latency: float  # 秒
    report: str
    task_success: float
    coverage: float
    tool_selection: float
    tool_argument: float
    requested_tools: tuple[str, ...]
    stream_calls: int
    complete_calls: int
    tokens: int
    mem_found: int  # MemoryEvent.found 合计
    kg_triples: int  # KgEvent.found_triples 合计
    judge_source: str  # rubric / llm / rubric_fallback


@dataclass(frozen=True)
class VariantResult:
    """一档变体在（适用它的）整份数据集上的聚合结果。"""

    variant: str
    n_items: int  # 该档实际跑了多少条（item.variants 过滤后可能 < 数据集条数）
    task_success: float  # 平均 task_success（离线=rubric；--real=LLM judge）
    tool_selection: float
    tool_argument: float
    error_rate: float
    coverage: float
    latency_p50: float  # 秒
    latency_p95: float  # 秒
    stream_calls_avg: float  # 平均 LLM 流式调用次数（graph=研究循环轮次；loop=整会话轮次）
    complete_calls_avg: float  # 平均 complete 调用次数（graph=plan+eval×n+[kg]+synth；loop=0）
    tokens_avg: float
    mem_found_avg: float  # 平均记忆召回条数（无 memory 的档恒 0）
    kg_triples_avg: float  # 平均 kg 命中三元组（无 kg / 无证据的档恒 0）
    judge: str  # 判定器来源（rubric 确定性 / llm DeepSeek）


def _validate_variants(names) -> list[str]:
    """过滤并校验变体名（未知名位置化报错，同 dataset 白名单纪律）。"""
    out = []
    for name in names:
        if name not in ALL_VARIANTS:
            raise ValueError(f"未知变体 {name!r}（合法：{', '.join(ALL_VARIANTS)}）")
        if name not in out:
            out.append(name)
    return out


def _make_driver(
    query: str, variant: Variant, ctx, *, max_iterations: int
) -> AsyncIterator[object]:
    """构造一次运行的 driver 生成器（懒导入：loop-only 离线跑不需 langgraph）。"""
    if variant.driver == "loop":
        from knowledge_pilot.agent.loop import run_research

        return run_research(query, llm=ctx.llm, search=ctx.search, mcp=ctx.mcp)
    from knowledge_pilot.agent.graph import run_research_graph

    return run_research_graph(
        query,
        llm=ctx.llm,
        search=ctx.search,
        rag=None,  # 评测只驱动 agent，不接 RAG 管线（与 loop 默认一致）
        max_iterations=max_iterations,
        memory=ctx.memory,
        memory_top_k=3,
        checkpoint_db=None,  # MemorySaver 即可；SQLite 持久化不是离线评测的诉求
        kg_enabled=variant.use_kg,
        kg_hops=2,
        mcp=ctx.mcp,
    )


async def _collect(agen: AsyncIterator[object]) -> tuple[list[object], float, str | None]:
    """消费整条事件流：计时 + 事件列表 + 错误类型（异常不中断矩阵，记入 error_rate）。"""
    events: list[object] = []
    error_kind: str | None = None
    start = time.perf_counter()
    try:
        async for evt in agen:
            events.append(evt)
    except Exception as exc:  # ValueError（未知工具）等：单条失败，不崩整场评测
        error_kind = type(exc).__name__
    latency = time.perf_counter() - start
    return events, latency, error_kind


async def _run_item(
    item: AgentItem, variant: Variant, ctx, judge, *, max_iterations: int
) -> ItemOutcome:
    """跑一条并打分。ctx（PreparedRun）已由 components 按 (item, variant) 建好并隔离。"""
    gen = _make_driver(item.query, variant, ctx, max_iterations=max_iterations)
    events, latency, error_kind = await _collect(gen)

    report = ""
    saw_done = False
    requested_raw: list[tuple[str, str]] = []  # (name, arguments JSON)
    requested_names: list[str] = []
    mem_found = 0
    kg_triples = 0
    for evt in events:
        if isinstance(evt, DoneEvent):
            report = evt.content
            saw_done = True
        elif isinstance(evt, ToolCallEvent):
            requested_raw.append((evt.name, evt.arguments))
            requested_names.append(evt.name)
        elif isinstance(evt, MemoryEvent):
            mem_found += evt.found
        elif isinstance(evt, KgEvent):
            kg_triples += evt.found_triples

    ok = error_kind is None and saw_done

    observed_calls = [
        (name, _try_loads(arguments)) for name, arguments in requested_raw
    ]
    item_coverage = coverage(item.must_include, report) if ok else 0.0

    verdict = await judge.score(item, report) if ok else None
    task_success = verdict.task_success if verdict is not None else 0.0
    judge_source = verdict.source if verdict is not None else "error"

    return ItemOutcome(
        query=item.query,
        ok=ok,
        error_kind=error_kind,
        latency=latency,
        report=report,
        task_success=task_success,
        coverage=item_coverage,
        tool_selection=tool_selection_accuracy(item.required_tools, requested_names),
        tool_argument=tool_argument_accuracy(item.expected_tool_calls, observed_calls),
        requested_tools=tuple(dict.fromkeys(requested_names)),
        stream_calls=ctx.llm.stream_calls,
        complete_calls=ctx.llm.complete_calls,
        tokens=ctx.llm.tokens,
        mem_found=mem_found,
        kg_triples=kg_triples,
        judge_source=judge_source,
    )


def _try_loads(arguments: str) -> dict:
    """ToolCallEvent.arguments 是 JSON 字符串；解析失败（理论上不该）退回 {}。"""
    try:
        parsed = json.loads(arguments)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


async def _drive_discard(
    variant: Variant, ctx, *, max_iterations: int
) -> None:
    """把一次完整运行跑完并丢弃结果（warmup：吸收懒导入/首编译，不测延迟）。"""
    gen = _make_driver("预热", variant, ctx, max_iterations=max_iterations)
    async for _ in gen:  # 异常直接冒泡：warmup 失败是 bug，不该被掩盖
        pass


async def run_agent_eval(
    dataset: AgentDataset,
    *,
    components,
    variants: list[str] | None = None,
    max_iterations: int = 3,
    warmup: bool = True,
) -> list[VariantResult]:
    """跑变体矩阵（默认五档；`variants` 可过滤），返回每档聚合结果。

    components 需满足：make_judge() / make_context(item, variant, *, max_iterations) /
    make_warmup_context(variant)（返回 None 表示该变体无需预热）。item.variants 过滤：
    条目可声明只在部分档跑（如 mcp_only_tool 条目只在 `all` 档有意义）。
    """
    results: list[VariantResult] = []
    for name in _validate_variants(list(variants) if variants is not None else ALL_VARIANTS):
        variant = ALL_VARIANTS[name]
        judge = components.make_judge()

        if warmup:
            warm = components.make_warmup_context(variant)
            if warm is not None:
                await _drive_discard(variant, warm, max_iterations=max_iterations)
                warm.close()

        outcomes: list[ItemOutcome] = []
        for item in dataset.items:
            if item.variants is not None and variant.name not in item.variants:
                continue  # 条目声明不在这档跑 → 不参与该档聚合
            ctx = components.make_context(item, variant, max_iterations=max_iterations)
            try:
                outcomes.append(
                    await _run_item(item, variant, ctx, judge, max_iterations=max_iterations)
                )
            finally:
                ctx.close()

        results.append(aggregate_variant(variant, outcomes, judge.llm_based))
    return results


def aggregate_variant(
    variant: Variant, outcomes: list[ItemOutcome], judge_llm_based: bool
) -> VariantResult:
    """把该档的 ItemOutcome 聚合成一行（mean / 百分位 / 错误率）。"""
    n = len(outcomes)
    if n == 0:
        return VariantResult(
            variant=variant.name, n_items=0, task_success=0.0, tool_selection=0.0,
            tool_argument=0.0, error_rate=0.0, coverage=0.0, latency_p50=0.0,
            latency_p95=0.0, stream_calls_avg=0.0, complete_calls_avg=0.0,
            tokens_avg=0.0, mem_found_avg=0.0, kg_triples_avg=0.0,
            judge="llm" if judge_llm_based else "rubric",
        )
    stats = latency_stats([o.latency for o in outcomes])
    return VariantResult(
        variant=variant.name,
        n_items=n,
        task_success=mean(o.task_success for o in outcomes),
        tool_selection=mean(o.tool_selection for o in outcomes),
        tool_argument=mean(o.tool_argument for o in outcomes),
        error_rate=sum(0 if o.ok else 1 for o in outcomes) / n,
        coverage=mean(o.coverage for o in outcomes),
        latency_p50=stats["p50"],
        latency_p95=stats["p95"],
        stream_calls_avg=mean(float(o.stream_calls) for o in outcomes),
        complete_calls_avg=mean(float(o.complete_calls) for o in outcomes),
        tokens_avg=mean(float(o.tokens) for o in outcomes),
        mem_found_avg=mean(float(o.mem_found) for o in outcomes),
        kg_triples_avg=mean(float(o.kg_triples) for o in outcomes),
        judge="llm" if judge_llm_based else "rubric",
    )


def format_results_table(results: list[VariantResult]) -> str:
    """手写对齐表格（沿用 rag/eval 风格，不引 tabulate）。延迟打印毫秒。"""
    canonical = list(ALL_VARIANTS)
    ordered = sorted(results, key=lambda r: canonical.index(r.variant))
    headers = [
        "variant", "n", "success", "sel", "arg", "err", "coverage",
        "lat_p50(ms)", "lat_p95(ms)", "stream", "complete", "tokens", "judge",
    ]
    rows = [
        [
            r.variant,
            str(r.n_items),
            f"{r.task_success:.2f}",
            f"{r.tool_selection:.2f}",
            f"{r.tool_argument:.2f}",
            f"{r.error_rate:.2f}",
            f"{r.coverage:.2f}",
            f"{r.latency_p50 * 1000:.1f}",
            f"{r.latency_p95 * 1000:.1f}",
            f"{r.stream_calls_avg:.1f}",
            f"{r.complete_calls_avg:.1f}",
            f"{r.tokens_avg:.0f}",
            r.judge,
        ]
        for r in ordered
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
