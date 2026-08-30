"""Phase 3 LangGraph 编排：图拓扑 / 条件循环 / 兜底 / 事件顺序 / 证据采集。

全程离线：FakeChatClient 脚本化 LLM，StubSearchProvider 不联网。
依赖 langgraph>=0.4（随 base dependencies 安装）。
"""

from knowledge_pilot.agent.events import (
    DoneEvent,
    EvalEvent,
    PlanEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient

PLANNER_JSON = '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}'
EVAL_SUFFICIENT = '{"sufficient": true, "reason": "资料足够", "gap": ""}'
EVAL_INSUFFICIENT = '{"sufficient": false, "reason": "缺对比", "gap": "需要补充对比数据"}'
REPORT = "# 研究报告\n这是最终报告。"

# 研究节点：无工具直答（拓扑测试用，1 次 stream_chat / 研究轮）
SCRIPT_DIRECT = [(["研究完成。"], [])]
# 研究节点：先 search_web 再总结（证据采集测试用，2 次 stream_chat / 研究轮）
SCRIPT_SEARCH = [
    ([], [{"name": "search_web", "arguments": '{"query": "资料"}'}]),
    (["本轮研究总结。"], []),
]


async def _run(query, llm, *, max_iterations=3):
    return [
        e
        async for e in run_research_graph(
            query,
            llm=llm,
            search=StubSearchProvider(),
            rag=None,
            max_iterations=max_iterations,
        )
    ]


def _fake(complete_script, stream_script):
    llm = FakeChatClient(script=stream_script)
    llm.complete_script = complete_script
    return llm


# ---- 图拓扑 -------------------------------------------------------------


async def test_sufficient_path_terminates_with_report():
    """资料充分：planner → research → evaluate{sufficient} → synthesize → END。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], SCRIPT_DIRECT)
    events = await _run("研究问题", llm)

    types = [type(e).__name__ for e in events]
    assert types == [
        "PlanEvent", "StatusEvent", "EvalEvent", "StatusEvent", "DoneEvent",
    ]

    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    assert plan_event.plan[0]["question"] == "子问题A"

    eval_event = next(e for e in events if isinstance(e, EvalEvent))
    assert eval_event.sufficient is True
    assert eval_event.iteration == 1

    assert events[-1].content == REPORT
    # planner + evaluate + synthesize = 3 次非流式调用
    assert llm.complete_calls == 3


async def test_insufficient_loops_back_then_synthesizes():
    """资料不足：evaluate 判 false + gap → 回到 research 再研究一轮 → 再评估充分 → 综合。"""
    llm = _fake(
        [PLANNER_JSON, EVAL_INSUFFICIENT, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH
    )
    events = await _run("研究问题", llm)

    # 两轮研究：每轮 StatusEvent(正在研究，收集资料…) 前后夹着 tool 事件
    research_status = [
        e for e in events if isinstance(e, StatusEvent) and "正在研究，" in e.message
    ]
    assert len(research_status) == 2

    evals = [e for e in events if isinstance(e, EvalEvent)]
    assert [e.iteration for e in evals] == [1, 2]
    assert evals[0].sufficient is False
    assert evals[1].sufficient is True

    assert isinstance(events[-1], DoneEvent)

    # 第二轮研究的查询应包含 evaluate 给的 refined_instruction
    gap_texts = [
        msg[1]["content"]
        for msg in llm.seen_messages
        if msg[0]["role"] == "system" and "研究执行助手" in msg[0]["content"]
    ]
    assert any("补充要求：需要补充对比数据" in text for text in gap_texts)

    # 证据跨两轮累计（reducer 生效）：综合报告的 evidence 含来源
    syn_msg = next(
        msg for msg in llm.seen_messages
        if msg[0]["role"] == "system" and "研究报告撰写员" in msg[0]["content"]
    )
    assert "来源：https://stub.example" in syn_msg[1]["content"]
    assert llm.complete_calls == 4  # planner + evaluate×2 + synthesize


async def test_iteration_cap_stops_loop():
    """全程不充分：迭代达到 max_iterations 后强制进入 synthesize（防死循环）。"""
    llm = _fake(
        [PLANNER_JSON, EVAL_INSUFFICIENT, EVAL_INSUFFICIENT, EVAL_INSUFFICIENT, REPORT],
        SCRIPT_DIRECT,
    )
    events = await _run("研究问题", llm, max_iterations=3)

    evals = [e for e in events if isinstance(e, EvalEvent)]
    assert len(evals) == 3
    assert all(e.sufficient is False for e in evals)
    assert isinstance(events[-1], DoneEvent)
    assert llm.calls == 3  # 恰好 3 轮研究（每轮 1 次 stream_chat，直答脚本）


# ---- 解析失败回退 -------------------------------------------------------


async def test_planner_json_failure_falls_back_to_single_step():
    """Planner 输出非 JSON：回退单步计划，图照常完成。"""
    llm = _fake(["这不是 JSON", EVAL_SUFFICIENT, REPORT], SCRIPT_DIRECT)
    events = await _run("研究问题", llm)

    plan_event = next(e for e in events if isinstance(e, PlanEvent))
    assert len(plan_event.plan) == 1
    assert plan_event.plan[0]["question"] == "研究问题"
    assert isinstance(events[-1], DoneEvent)


async def test_evaluate_json_failure_defaults_sufficient():
    """Evaluate 输出非 JSON：默认视为充分（推进而非无限循环）。"""
    llm = _fake([PLANNER_JSON, "乱码输出", REPORT], SCRIPT_DIRECT)
    events = await _run("研究问题", llm)

    eval_event = next(e for e in events if isinstance(e, EvalEvent))
    assert eval_event.sufficient is True
    assert isinstance(events[-1], DoneEvent)


# ---- 证据采集与事件顺序 -------------------------------------------------


async def test_evidence_collected_from_search_results():
    """search_web 的原始结构化结果经钩子进入 evidence，供综合报告引用。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH)
    events = await _run("研究问题", llm)

    syn_msg = next(
        msg for msg in llm.seen_messages
        if msg[0]["role"] == "system" and "研究报告撰写员" in msg[0]["content"]
    )
    # 至少 1 条来源被格式化进综合报告的输入
    assert syn_msg[1]["content"].count("来源：https://stub.example") >= 1
    # 工具事件确实出现在事件流里
    assert any(isinstance(e, ToolCallEvent) for e in events)
    assert any(isinstance(e, ToolResultEvent) for e in events)


async def test_events_do_not_cross_rounds():
    """事件不串轮：本轮 eval 后、下一轮 research 的 tool_call 之前，顺序正确。"""
    llm = _fake(
        [PLANNER_JSON, EVAL_INSUFFICIENT, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH
    )
    events = await _run("研究问题", llm)

    tool_call_idxs = [i for i, e in enumerate(events) if isinstance(e, ToolCallEvent)]
    eval_idxs = [i for i, e in enumerate(events) if isinstance(e, EvalEvent)]
    assert len(tool_call_idxs) == 2 and len(eval_idxs) == 2
    # 第 1 轮 tool_call 早于第 1 次 eval；第 2 轮 tool_call 晚于第 1 次 eval
    assert tool_call_idxs[0] < eval_idxs[0] < tool_call_idxs[1] < eval_idxs[1]
