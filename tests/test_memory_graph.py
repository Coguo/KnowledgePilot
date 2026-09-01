"""Phase 4 Memory × 图：记忆召回注入 planner / 研究后落库 / SqliteSaver 持久化路径。

全程离线：FakeChatClient 脚本化 LLM + StubSearchProvider + tmp_path 临时 SQLite。
依赖 langgraph>=0.4；未装 langgraph-checkpoint-sqlite 时 SqliteSaver 路径自动回退
MemorySaver（行为仍可测）。
"""

from knowledge_pilot.agent.events import DoneEvent, MemoryEvent
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.memory import create_memory_store
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient

PLANNER_JSON = '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}'
EVAL_SUFFICIENT = '{"sufficient": true, "reason": "资料足够", "gap": ""}'
REPORT = "# 研究报告\n这是最终报告。"
# 研究节点：先 search_web 再总结（证据采集走 on_search_results 钩子）
SCRIPT_SEARCH = [
    ([], [{"name": "search_web", "arguments": '{"query": "资料"}'}]),
    (["本轮研究总结。"], []),
]


async def _run(query, llm, *, memory=None, memory_top_k=3, checkpoint_db=None):
    return [
        e
        async for e in run_research_graph(
            query,
            llm=llm,
            search=StubSearchProvider(),
            rag=None,
            max_iterations=3,
            memory=memory,
            memory_top_k=memory_top_k,
            checkpoint_db=checkpoint_db,
        )
    ]


def _fake(complete_script, stream_script):
    llm = FakeChatClient(script=stream_script)
    llm.complete_script = complete_script
    return llm


def _planner_msg(llm):
    return next(
        msg
        for msg in llm.seen_messages
        if msg[0]["role"] == "system" and "研究规划助手" in msg[0]["content"]
    )


async def test_memory_recall_injects_context_and_emits_event(tmp_path):
    store = create_memory_store(str(tmp_path / "memory.db"))
    store.save_run(
        "RAG chunking 策略",
        plan=[],
        evidence=[],
        report="fixed 与 recursive 对比结论",
        sources=[{"title": "t", "url": "https://a.example"}],
    )
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH)
    events = await _run("比较 chunking 策略", llm, memory=store, memory_top_k=3)

    mem_events = [e for e in events if isinstance(e, MemoryEvent)]
    assert len(mem_events) == 1
    assert mem_events[0].found == 1

    # planner 的 user 消息应包含历史研究背景（含历史 query）
    user_content = _planner_msg(llm)[1]["content"]
    assert "历史研究背景" in user_content
    assert "RAG chunking 策略" in user_content
    assert isinstance(events[-1], DoneEvent)
    store.close()


async def test_run_saves_research_record(tmp_path):
    store = create_memory_store(str(tmp_path / "memory.db"))
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH)
    events = await _run("研究 LangGraph 编排", llm, memory=store)

    assert isinstance(events[-1], DoneEvent)
    assert store.count() == 1
    run = store.recent(1)[0]
    assert run["query"] == "研究 LangGraph 编排"
    assert run["plan"][0]["question"] == "子问题A"
    assert run["report"] == REPORT
    # 来源来自 search_web 证据（StubSearchProvider 的 URL 含 stub.example）
    assert run["sources"] and any("stub.example" in s["url"] for s in run["sources"])
    store.close()


async def test_sources_deduped(tmp_path):
    store = create_memory_store(str(tmp_path / "memory.db"))
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH)
    await _run("研究问题", llm, memory=store)
    urls = [s["url"] for s in store.recent(1)[0]["sources"]]
    assert urls
    assert len(urls) == len(set(urls))
    store.close()


async def test_no_memory_keeps_phase3_behavior():
    """memory=None：无 MemoryEvent、planner 消息无历史背景（Phase 3 逐字节一致）。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH)
    events = await _run("研究问题", llm, memory=None)

    assert not any(isinstance(e, MemoryEvent) for e in events)
    assert "历史研究背景" not in _planner_msg(llm)[1]["content"]
    assert isinstance(events[-1], DoneEvent)


async def test_persistent_checkpoint_path_completes(tmp_path):
    """memory + checkpoint_db：流正常完成且落库；未装 checkpoint-sqlite 时回退 MemorySaver。"""
    store = create_memory_store(str(tmp_path / "memory.db"))
    checkpoint_db = str(tmp_path / "graph_checkpoints.db")
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], SCRIPT_SEARCH)
    events = await _run("研究 LangGraph", llm, memory=store, checkpoint_db=checkpoint_db)

    assert isinstance(events[-1], DoneEvent)
    assert store.count() == 1
    store.close()
