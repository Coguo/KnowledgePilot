"""Agent Evaluation runner B 轨测试（需 langgraph：图驱动/完整五档矩阵端到端）。

覆盖：
- **graph-drift canary**：直接驱动 run_research_graph，断言 ScriptedChatClient 消费的
  complete/stream 次数与脚本长度严格一致（graph 改动会显式打破——eval 信任可验证）。
- **完整矩阵聚合**：small.json 五档离线跑——mcp_only 条目的可用性轴（非 mcp 档 err、
  all 档全过）、memory 档只在该档召回>0、kg 档在证据条目上多一次 complete、
  字节确定性（monkeypatch perf_counter，两跑各分完全相等）。
"""

import asyncio

import pytest

from knowledge_pilot.agent.eval import runner as runner_mod
from knowledge_pilot.agent.eval.dataset import load_dataset
from knowledge_pilot.agent.eval.metrics import coverage
from knowledge_pilot.agent.eval.offline import (
    ALL_VARIANTS,
    ScriptedChatClient,
    StubMCPGateway,
    build_script_plan,
    make_offline_components,
)
from knowledge_pilot.agent.events import DoneEvent, KgEvent
from knowledge_pilot.search.stub import StubSearchProvider

# graph 模块依赖 langgraph：须在导入它之前先 importorskip，否则缺依赖时收集期即炸。
pytest.importorskip("langgraph")

from knowledge_pilot.agent.graph import run_research_graph  # noqa: E402

FIXTURE = "tests/fixtures/eval_agent/small.json"


# ---- graph-drift canary：driver 消费与脚本长度严格一致 ----------------------


async def test_graph_ideal_driver_consumes_exact_script():
    ds = load_dataset(FIXTURE)
    item = ds.items[0]  # ideal search_web 条目
    plan = build_script_plan(item, ALL_VARIANTS["graph"])
    llm = ScriptedChatClient(plan.stream, plan.complete)
    events = [
        e
        async for e in run_research_graph(
            item.query, llm=llm, search=StubSearchProvider(), max_iterations=3
        )
    ]
    assert llm.complete_calls == 3  # planner + evaluate + synthesize
    assert llm.stream_calls == 2  # 搜索 + 总结
    done = [e for e in events if isinstance(e, DoneEvent)][-1]
    assert done.content == plan.report
    assert coverage(item.must_include, done.content) == 1.0


async def test_graph_kg_driver_adds_one_complete_and_emits_kg():
    ds = load_dataset(FIXTURE)
    item = ds.items[0]
    plan = build_script_plan(item, ALL_VARIANTS["graph+kg"])
    llm = ScriptedChatClient(plan.stream, plan.complete)
    events = [
        e
        async for e in run_research_graph(
            item.query,
            llm=llm,
            search=StubSearchProvider(),
            max_iterations=3,
            kg_enabled=True,
        )
    ]
    assert llm.complete_calls == 4  # planner + evaluate + kg 抽取 + synthesize
    kgs = [e for e in events if isinstance(e, KgEvent)]
    assert len(kgs) == 1
    assert kgs[0].entities >= 1  # 证据存在 → kg 节点真的调了 LLM 并建图


async def test_graph_all_mcp_item_succeeds_via_stub_gateway():
    ds = load_dataset(FIXTURE)
    item = ds.items[2]  # mcp_only_tool：search_papers 只在带 mcp 的档可用
    variant = ALL_VARIANTS["all"]
    plan = build_script_plan(item, variant)
    llm = ScriptedChatClient(plan.stream, plan.complete)
    mcp = StubMCPGateway()
    events = [
        e
        async for e in run_research_graph(
            item.query,
            llm=llm,
            search=StubSearchProvider(),
            max_iterations=3,
            mcp=mcp,
        )
    ]
    assert llm.complete_calls == 3  # kg 无证据 → 条目省略，只有 plan+eval+synth
    assert mcp.calls[0][0] == "search_papers"
    done = [e for e in events if isinstance(e, DoneEvent)][-1]
    assert done.content == plan.report


# ---- 完整矩阵端到端聚合 -----------------------------------------------------


def _run_matrix():
    return asyncio.run(
        runner_mod.run_agent_eval(
            load_dataset(FIXTURE), components=make_offline_components()
        )
    )


def _row(results, name):
    return next(r for r in results if r.variant == name)


def test_matrix_axis_mcp_item_errors_except_all():
    results = _run_matrix()
    assert [r.n_items for r in results] == [4] * 5  # 数据集条目全在五档跑
    for name in ("loop", "graph", "graph+memory", "graph+kg"):
        assert _row(results, name).error_rate == pytest.approx(0.25)  # 仅 mcp_only 条目
    assert _row(results, "all").error_rate == 0.0  # mcp 网关在 → search_papers 可用
    assert _row(results, "all").task_success == 1.0


def test_matrix_memory_only_recalls_in_memory_variants():
    results = _run_matrix()
    # 记忆条目（item4）只在启用 memory 的档召回>0：1 条 / 4 条 → avg 0.25
    for name in ("graph+memory", "all"):
        assert _row(results, name).mem_found_avg == pytest.approx(0.25)
    for name in ("loop", "graph", "graph+kg"):
        assert _row(results, name).mem_found_avg == 0.0


def test_matrix_kg_adds_complete_only_on_evidence_items():
    results = _run_matrix()
    graph = _row(results, "graph")
    kg = _row(results, "graph+kg")
    # 3 条有结构化证据的条目各 +1（理想/iterate/memory），mcp_only 无证据不加
    assert kg.complete_calls_avg - graph.complete_calls_avg == pytest.approx(0.75)
    # memory 不加任何 LLM 调用 → 与 graph 相同
    assert _row(results, "graph+memory").complete_calls_avg == graph.complete_calls_avg


def test_matrix_is_byte_deterministic(monkeypatch):
    # 冻结时钟：两跑延迟一致 → 连 latency 在内的全部分数逐字段相等（可复现）。
    calls = {"n": 0}

    def fake_perf_counter():
        calls["n"] += 1
        # 整数秒：每次测量的差值恒为 1.0，无二进制浮点残差。若用 0.001 这类值，
        # (n+1)*0.001 - n*0.001 会引入 1e-18 级误差 → p50/p95 两跑不等，
        # 逐字节确定性被时钟而非被测逻辑破坏。
        return float(calls["n"])

    monkeypatch.setattr(runner_mod.time, "perf_counter", fake_perf_counter)
    a = _run_matrix()
    b = _run_matrix()
    assert len(a) == len(b) == 5
    for ra, rb in zip(a, b):
        assert ra == rb
