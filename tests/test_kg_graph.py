"""Phase 5 Knowledge Graph × LangGraph 编排：注入 synthesize / 事件 / 向后兼容。

全程离线：FakeChatClient 脚本化 LLM（complete_script 驱动 planner/evaluate/kg/
synthesize，script 驱动研究节点 tool 循环），StubSearchProvider 不联网。
依赖 langgraph>=0.4（随 base dependencies 安装）。
"""

from knowledge_pilot.agent.events import DoneEvent, KgEvent, StatusEvent
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient

PLANNER_JSON = '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}'
EVAL_SUFFICIENT = '{"sufficient": true, "reason": "资料足够", "gap": ""}'
EVAL_INSUFFICIENT = '{"sufficient": false, "reason": "缺对比", "gap": "需要补充对比数据"}'
REPORT = "# 研究报告\n这是最终报告。"
KG_EXTRACT_JSON = (
    '{"entities": [{"name": "RAG", "type": "concept"}, '
    '{"name": "Embedding", "type": "method"}], '
    '"relations": [{"source": "RAG", "target": "Embedding", "relation": "uses"}]}'
)
KG_EMPTY_JSON = '{"entities": [], "relations": []}'

# 研究节点：先 search_web 再总结（这样 evidence 有内容可抽取）
SCRIPT_SEARCH = [
    ([], [{"name": "search_web", "arguments": '{"query": "资料"}'}]),
    (["本轮研究总结。"], []),
]


async def _run(query, llm, *, kg_enabled=False, kg_hops=2):
    return [
        e
        async for e in run_research_graph(
            query,
            llm=llm,
            search=StubSearchProvider(),
            rag=None,
            max_iterations=3,
            kg_enabled=kg_enabled,
            kg_hops=kg_hops,
        )
    ]


def _fake(complete_script):
    llm = FakeChatClient(script=SCRIPT_SEARCH)
    llm.complete_script = complete_script
    return llm


def _synthesize_user_content(llm) -> str:
    """取 synthesize 节点（system="研究报告撰写员"）对应的 user 消息内容。"""
    msg = next(
        msg for msg in llm.seen_messages
        if msg[0]["role"] == "system" and "研究报告撰写员" in msg[0]["content"]
    )
    return msg[1]["content"]


# ---- KG 启用：注入 synthesize + 事件 -------------------------------------


async def test_kg_enabled_builds_context_and_emits_event():
    """KG 启用：抽取 RAG/Embedding 建图 → 关键词命中 → BFS → 三元组注入 synthesize。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, KG_EXTRACT_JSON, REPORT])
    events = await _run("RAG 与 Embedding 的关系", llm, kg_enabled=True)

    kg_events = [e for e in events if isinstance(e, KgEvent)]
    assert len(kg_events) == 1
    assert kg_events[0].entities == 2
    assert kg_events[0].relations == 1
    assert kg_events[0].found_triples == 1  # rag -[uses]-> embedding 去重后 1 条

    content = _synthesize_user_content(llm)
    assert "相关实体关系（知识图谱）" in content
    assert "- rag -[uses]-> embedding" in content  # 归一化小写

    assert isinstance(events[-1], DoneEvent)
    # planner + evaluate + kg + synthesize = 4 次非流式调用（防 FakeChatClient 末尾复用 REPORT）
    assert llm.complete_calls == 4


async def test_kg_disabled_matches_phase3_without_event():
    """KG 禁用（默认）：无 KgEvent、synthesize 无图谱块，行为与 Phase 3 一致。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT])
    events = await _run("研究问题", llm, kg_enabled=False)

    assert not any(isinstance(e, KgEvent) for e in events)
    assert "相关实体关系（知识图谱）" not in _synthesize_user_content(llm)
    assert isinstance(events[-1], DoneEvent)
    assert llm.complete_calls == 3  # planner + evaluate + synthesize


async def test_kg_empty_result_is_byte_identical_to_disabled():
    """KG 启用但没抽到实体 → KgEvent(0,0,0)，且 synthesize 输入与禁用时逐字节一致。"""
    llm_off = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT])
    await _run("研究问题", llm_off, kg_enabled=False)
    off_content = _synthesize_user_content(llm_off)

    llm_on = _fake([PLANNER_JSON, EVAL_SUFFICIENT, KG_EMPTY_JSON, REPORT])
    events = await _run("研究问题", llm_on, kg_enabled=True)
    on_content = _synthesize_user_content(llm_on)

    kg_events = [e for e in events if isinstance(e, KgEvent)]
    assert len(kg_events) == 1 and kg_events[0] == KgEvent(entities=0, relations=0, found_triples=0)
    assert on_content == off_content  # 空图谱不改变 synthesize 输入


async def test_kg_extraction_failure_does_not_block_synthesis():
    """抽取输出非 JSON：KG 跳过，报告照常生成。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, "这不是 JSON", REPORT])
    events = await _run("研究问题", llm, kg_enabled=True)

    kg_events = [e for e in events if isinstance(e, KgEvent)]
    assert len(kg_events) == 1 and kg_events[0] == KgEvent(entities=0, relations=0, found_triples=0)
    assert "相关实体关系（知识图谱）" not in _synthesize_user_content(llm)
    assert events[-1].content == REPORT
    assert llm.complete_calls == 4


# ---- 循环与事件顺序 -------------------------------------------------------


async def test_kg_runs_once_even_with_research_loop():
    """研究循环多次（不充分→充分）时，KG 只构建一次（每图运行一次，非每轮）。"""
    llm = _fake([PLANNER_JSON, EVAL_INSUFFICIENT, EVAL_SUFFICIENT, KG_EXTRACT_JSON, REPORT])
    events = await _run("RAG 与 Embedding 的关系", llm, kg_enabled=True)

    kg_events = [e for e in events if isinstance(e, KgEvent)]
    assert len(kg_events) == 1
    assert llm.complete_calls == 5  # planner + evaluate×2 + kg + synthesize


async def test_kg_status_event_precedes_event():
    """事件顺序：StatusEvent("正在构建知识图谱…") 早于 KgEvent。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, KG_EXTRACT_JSON, REPORT])
    events = await _run("RAG 与 Embedding 的关系", llm, kg_enabled=True)

    status_idxs = [
        i for i, e in enumerate(events)
        if isinstance(e, StatusEvent) and "构建知识图谱" in e.message
    ]
    kg_idxs = [i for i, e in enumerate(events) if isinstance(e, KgEvent)]
    assert status_idxs and kg_idxs
    assert status_idxs[0] < kg_idxs[0]
