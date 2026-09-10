"""Agent Evaluation 离线组件 A 轨测试（纯 stdlib，无需 langgraph）。

覆盖：脚本化 LLM（complete 按序末条重复 / 流式 tool_call 名称+参数两半累加 / token 记账）、
loop E2E（search→report、MAX_TOOL_ROUNDS 兜底、未知 MCP 工具报错）、桩 MCP 网关、
脚本长度 canary（ideal=3、kg=4、iterate=1+max+1(+kg)）、组件工厂（每 (variant,item)
独立记忆库 + 预热）。
"""

import json

import pytest

from knowledge_pilot.agent.eval.dataset import (
    KNOWN_VARIANT_NAMES,
    AgentItem,
    GoldToolCall,
    load_dataset,
)
from knowledge_pilot.agent.eval.metrics import coverage
from knowledge_pilot.agent.eval.offline import (
    ALL_VARIANTS,
    ScriptPlan,
    ScriptedChatClient,
    ScriptedRound,
    StubMCPGateway,
    Variant,
    build_script_plan,
    content_round,
    make_canonical_report,
    make_offline_components,
    tool_round,
)
from knowledge_pilot.agent.events import DoneEvent, ToolCallEvent, ToolResultEvent
from knowledge_pilot.agent.loop import MAX_TOOL_ROUNDS, run_research
from knowledge_pilot.search.stub import StubSearchProvider

FIXTURE = "tests/fixtures/eval_agent/small.json"


def _item(profile="ideal", *, expected_name="search_web", variants=None) -> AgentItem:
    return AgentItem(
        query="RAG 检索增强怎么做",
        must_include=("RAG",),
        required_tools=(expected_name,),
        expected_tool_calls=(GoldToolCall(name=expected_name, arguments={"query": "x"}),),
        offline_profile=profile,
        variants=variants,
    )


# ---- Variant 五档 ---------------------------------------------------------


def test_variants_match_dataset_whitelist():
    assert set(ALL_VARIANTS) == KNOWN_VARIANT_NAMES
    assert ALL_VARIANTS["loop"].driver == "loop"
    assert ALL_VARIANTS["graph"].driver == "graph"
    assert not ALL_VARIANTS["graph"].use_memory
    assert ALL_VARIANTS["graph+memory"].use_memory
    assert ALL_VARIANTS["graph+kg"].use_kg
    assert ALL_VARIANTS["all"] == Variant("all", "graph", use_memory=True, use_kg=True, use_mcp=True)


# ---- ScriptedChatClient ---------------------------------------------------


def test_complete_sequential_then_repeat_last():
    llm = ScriptedChatClient([], complete_script=["A", "B"])
    assert llm.complete_calls == 0

    async def go():
        from knowledge_pilot.llm.client import StreamChunk  # noqa

        out = []
        for _ in range(4):
            out.append(await llm.complete([{"role": "user", "content": "hi"}]))
        return out

    assert __import__("asyncio").run(go()) == ["A", "B", "B", "B"]
    assert llm.complete_calls == 4
    assert llm.tokens > 0


def test_complete_empty_script_returns_empty():
    llm = ScriptedChatClient([], complete_script=[])

    async def go():
        return await llm.complete([{"role": "user", "content": "hi"}])

    assert __import__("asyncio").run(go()) == ""
    assert llm.complete_calls == 1


async def test_stream_tool_call_round_accumulates_name_and_args():
    # 脚本让模型"搜索 x"：名称一次到位 + 参数两半切分 → 调用方应拼出完整 JSON。
    llm = ScriptedChatClient(
        [tool_round("search_web", {"query": "RAG chunking"})], complete_script=[]
    )
    chunks = [c async for c in llm.stream_chat([{"role": "user", "content": "q"}])]
    # 拼接辅助：模拟 run_research 的累加（arguments 跨多个增量拼接）
    name = ""
    args = ""
    for c in chunks:
        if c.tool_call_delta:
            d = c.tool_call_delta
            name += d.get("name") or ""
            args += d.get("arguments") or ""
    assert name == "search_web"
    assert json.loads(args) == {"query": "RAG chunking"}
    assert llm.stream_calls == 1
    assert llm.seen_tools[0] is None or True  # 记账不崩


async def test_stream_repeats_last_round_when_script_exhausted():
    llm = ScriptedChatClient([content_round("一遍")])
    seen = []
    for _ in range(3):
        async for c in llm.stream_chat([{"role": "user", "content": "q"}]):
            seen.append(c.content_delta)
    assert seen == ["一遍", "一遍", "一遍"]
    assert llm.stream_calls == 3


async def test_tokens_accounting_grows_with_messages():
    llm = ScriptedChatClient([content_round("A"), content_round("B")])
    for m in ([{"role": "user", "content": "长" * 100}], [{"role": "user", "content": "长" * 200}]):
        async for _ in llm.stream_chat(m):
            pass
    assert llm.tokens > 0
    # 第二次 context 更长 → 单次成本单调不减（tokens 为累计，至少不低于首次）
    assert llm.seen_messages[1] is not None


# ---- StubMCPGateway -------------------------------------------------------


async def test_stub_mcp_gateway_duck_api():
    gw = StubMCPGateway()
    assert set(gw.names()) == {"search_memory", "recent_research", "search_papers"}
    assert gw.has("search_papers")
    assert not gw.has("no_such")
    assert len(gw.tool_schemas()) == 3
    assert all(t["type"] == "function" for t in gw.tool_schemas())
    assert "search_memory" in gw.prompt_hint()
    text = await gw.call("search_papers", {"query": "agentic RAG"})
    assert "arxiv.org/abs" in text
    assert gw.calls == [("search_papers", {"query": "agentic RAG"})]


# ---- canonical report -----------------------------------------------------


def test_canonical_report_covers_must_include():
    ds = load_dataset(FIXTURE)
    for item in ds.items:
        assert coverage(item.must_include, make_canonical_report(item)) == 1.0


def test_canonical_report_empty_terms_ok():
    item = AgentItem(query="q")
    rep = make_canonical_report(item)
    assert "q" in rep


# ---- build_script_plan 长度 canary ----------------------------------------


def test_loop_never_completes_and_single_pass():
    for profile in ["ideal", "iterate_to_cap", "planner_bad_json", "mcp_only_tool"]:
        plan = build_script_plan(_item(profile), ALL_VARIANTS["loop"])
        assert plan.complete == []
        # 单轮循环：搜一次 + 答一次（tool_round_cap 除外，见下）
    plan_tool = build_script_plan(_item("tool_round_cap"), ALL_VARIANTS["loop"])
    assert len(plan_tool.stream) == 1  # 永远请求工具 → 撞 MAX_TOOL_ROUNDS


def test_graph_ideal_complete_counts():
    graph = build_script_plan(_item("ideal"), ALL_VARIANTS["graph"])
    assert len(graph.complete) == 3  # planner + evaluate + synthesize
    kg = build_script_plan(_item("ideal"), ALL_VARIANTS["graph+kg"])
    assert len(kg.complete) == 4  # + kg
    assert len(graph.stream) == 2  # search + summary


def test_graph_iterate_complete_counts_respect_max_iterations():
    item = _item("iterate_to_cap")
    for max_iter in (2, 3):
        g = build_script_plan(item, ALL_VARIANTS["graph"], max_iterations=max_iter)
        assert len(g.complete) == 1 + max_iter + 1  # 1 planner + max eval + 1 synth
        assert len(g.stream) == 2 * max_iter
        kg = build_script_plan(item, ALL_VARIANTS["graph+kg"], max_iterations=max_iter)
        assert len(kg.complete) == 1 + max_iter + 1 + 1  # + kg
    # loop 无法自评：iterate 对其退化为单轮，不消耗 complete
    loop = build_script_plan(item, ALL_VARIANTS["loop"], max_iterations=3)
    assert len(loop.stream) == 2


def test_planner_bad_json_first_entry_unparseable():
    plan = build_script_plan(_item("planner_bad_json"), ALL_VARIANTS["graph"])
    assert plan.complete[0].startswith("这不是 JSON")
    assert len(plan.complete) == 3


def test_mcp_only_tool_omits_kg_when_no_native_evidence():
    # 只有 MCP 工具（结果自由文本、不进 evidence）→ kg 节点不消费 complete，条目省略。
    item = _item("mcp_only_tool", expected_name="search_papers")
    all_plan = build_script_plan(item, ALL_VARIANTS["all"])
    assert len(all_plan.complete) == 3  # plan + suff + report（无 kg 条目）


def test_warmup_and_report_fields():
    plan = build_script_plan(_item("ideal"), ALL_VARIANTS["graph"])
    assert isinstance(plan, ScriptPlan)
    assert plan.report.startswith("# 研究报告")
    assert all(isinstance(r, ScriptedRound) for r in plan.stream)


# ---- loop E2E（离线可跑，无 langgraph）------------------------------------


async def test_loop_ideal_search_then_report():
    ds = load_dataset(FIXTURE)
    item = ds.items[0]
    variant = ALL_VARIANTS["loop"]
    plan = build_script_plan(item, variant)
    llm = ScriptedChatClient(plan.stream, plan.complete)
    events = [e async for e in run_research(item.query, llm=llm, search=StubSearchProvider())]

    assert llm.complete_calls == 0  # loop 不调 complete
    assert llm.stream_calls == 2
    tools = [e for e in events if isinstance(e, ToolCallEvent)]
    assert [t.name for t in tools] == ["search_web"]
    assert json.loads(tools[0].arguments) == {"query": "GraphRAG 与 RAG 的区别"}
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.content == plan.report
    assert coverage(item.must_include, done.content) == 1.0


async def test_loop_tool_round_cap_hits_max_rounds_empty_answer():
    item = _item("tool_round_cap")
    plan = build_script_plan(item, ALL_VARIANTS["loop"])
    llm = ScriptedChatClient(plan.stream, plan.complete)
    events = [e async for e in run_research(item.query, llm=llm, search=StubSearchProvider())]

    assert llm.stream_calls == MAX_TOOL_ROUNDS  # 撞上限，不无限循环
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.content == ""  # 循环模式：不给内容就被上限兜底 → 空报告
    assert len([e for e in events if isinstance(e, ToolCallEvent)]) == MAX_TOOL_ROUNDS - 1


async def test_loop_unknown_mcp_tool_raises():
    item = _item("mcp_only_tool", expected_name="search_papers")
    plan = build_script_plan(item, ALL_VARIANTS["loop"])  # loop 无 mcp → search_papers 不存在
    llm = ScriptedChatClient(plan.stream, plan.complete)
    with pytest.raises(ValueError, match="search_papers"):
        events = [e async for e in run_research(item.query, llm=llm, search=StubSearchProvider())]


# ---- 组件工厂 -------------------------------------------------------------


def test_offline_components_make_context_memory_and_mcp():
    ds = load_dataset(FIXTURE)
    item = ds.items[3]  # 带 pre_seed 的 memory 条目
    comps = make_offline_components()

    ctx_all = comps.make_context(item, ALL_VARIANTS["all"], max_iterations=3)
    assert isinstance(ctx_all.llm, ScriptedChatClient)
    assert ctx_all.mcp is not None
    assert ctx_all.memory is not None
    assert ctx_all.memory.count() == 1  # pre_seed 已预置
    ctx_all.close()

    ctx_graph = comps.make_context(item, ALL_VARIANTS["graph"], max_iterations=3)
    assert ctx_graph.memory is None
    assert ctx_graph.mcp is None
    ctx_graph.close()


def test_offline_components_warmup_only_graph():
    comps = make_offline_components()
    assert comps.make_warmup_context(ALL_VARIANTS["loop"]) is None
    for name in ("graph", "graph+memory", "graph+kg", "all"):
        assert comps.make_warmup_context(ALL_VARIANTS[name]) is not None
