"""Phase 6 MCP × LangGraph 编排：notes 落库 / 工具事件 / 向后兼容。

全程离线：FakeChatClient 脚本化 LLM，FakeMCPGateway 鸭子类型模拟 stdio 网关
（不 import mcp），StubSearchProvider 不联网。依赖 langgraph>=0.4（随 base
dependencies 安装，本地未装时延后执行——同 Phase 3/4/5 graph 测试）。

语义红线：MCP 工具结果（自由文本）只进 notes / 报告「工具补充资料」块，
**不进 evidence / 来源列表**（memory 落库 sources 不含 arXiv URL）。
"""

from knowledge_pilot.agent.events import DoneEvent, ToolCallEvent, ToolResultEvent
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.agent.tools import ALL_TOOLS, SEARCH_WEB_TOOL
from knowledge_pilot.memory import create_memory_store
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient, FakeMCPGateway

PLANNER_JSON = '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}'
EVAL_SUFFICIENT = '{"sufficient": true, "reason": "资料足够", "gap": ""}'
EVAL_INSUFFICIENT = '{"sufficient": false, "reason": "缺资料", "gap": "需要更多信息"}'
REPORT = "# 研究报告\n这是最终报告。"

MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": "在用户历史研究记录中召回相关条目",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
            "required": ["query"],
        },
    },
}
PAPERS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_papers",
        "description": "在 arXiv 检索论文",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}

# 研究节点：只调 search_web（无 MCP 时用——MCP 关闭/空网关下模型拿不到 MCP schema，
# 脚本里再出现 MCP 工具名会走 _dispatch_tool 的「未知工具」ValueError，那是别处的契约）。
SCRIPT_WEB_ONLY = [
    ([], [{"name": "search_web", "arguments": '{"query": "RAG chunking 资料"}'}]),
    (["本轮研究总结。"], []),
]
# 研究节点：先调 search_web（采证）+ search_papers（MCP，落 notes）再总结。
SCRIPT_WEB_AND_MCP = [
    (
        [],
        [
            {"name": "search_web", "arguments": '{"query": "RAG chunking 资料"}'},
            {"name": "search_papers", "arguments": '{"query": "RAG chunking"}'},
        ],
    ),
    (["本轮研究总结。"], []),
]
# 跨研究轮：round1 调 search_web（采证）+ memory、round2 调 papers（notes 跨轮累计）。
# 必须带上 search_web：下面要验证「MCP 的 arXiv URL 不进 sources」，前提是本轮真的
# 有网页来源可对照——纯 MCP 调用不产生 evidence，sources 为空会让该断言失去意义。
SCRIPT_TWO_ROUNDS = [
    (
        [],
        [
            {"name": "search_web", "arguments": '{"query": "RAG chunking"}'},
            {"name": "search_memory", "arguments": '{"query": "RAG chunking"}'},
        ],
    ),
    (["第一轮研究总结。"], []),
    ([], [{"name": "search_papers", "arguments": '{"query": "RAG chunking"}'}]),
    (["第二轮研究总结。"], []),
]

MCP_RESPONSES = {
    "search_memory": "历史研究：2026-09-01「RAG chunking」→ 结论 chunk 策略对比。",
    "search_papers": (
        "相关论文：[1] arXiv RAG chunking\n"
        "    URL: https://arxiv.org/abs/2401.01234\n"
        "    摘要：……"
    ),
}


def _synthesize_user_content(llm) -> str:
    """取 synthesize 节点（system="研究报告撰写员"）对应的 user 消息内容。"""
    msg = next(
        msg for msg in llm.seen_messages
        if msg[0]["role"] == "system" and "研究报告撰写员" in msg[0]["content"]
    )
    return msg[1]["content"]


def _tool_names(seen) -> list[str]:
    """从一次 stream_chat 收到的 tools 里取工具名。"""
    return [t["function"]["name"] for t in seen]


async def _run(query, llm, *, mcp=None, memory=None, tmp_path=None):
    return [
        e
        async for e in run_research_graph(
            query,
            llm=llm,
            search=StubSearchProvider(),
            rag=None,
            max_iterations=3,
            memory=memory,
            memory_top_k=3,
            checkpoint_db=str(tmp_path / "graph.db") if tmp_path else None,
            mcp=mcp,
        )
    ]


def _fake(complete_script, script=SCRIPT_WEB_AND_MCP):
    llm = FakeChatClient(script=script)
    llm.complete_script = complete_script
    return llm


# ---- 向后兼容：禁用 / 空网关路径与 Phase 5 逐字节一致 ---------------------


async def test_mcp_disabled_matches_phase5():
    """mcp=None（默认）：工具列表仍是 ALL_TOOLS、无 notes 块、调用数不变。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], script=SCRIPT_WEB_ONLY)
    events = await _run("研究问题", llm, mcp=None)

    assert llm.seen_tools[-1] == [SEARCH_WEB_TOOL]  # 与 Phase 5 相同的 ALL_TOOLS
    assert "工具补充资料（MCP" not in _synthesize_user_content(llm)
    assert isinstance(events[-1], DoneEvent)
    assert llm.complete_calls == 3  # planner + evaluate + synthesize


async def test_mcp_gateway_with_no_tools_is_disabled():
    """网关已开但没连到任何工具（server 全失败）→ 与禁用一致。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT], script=SCRIPT_WEB_ONLY)
    gw = FakeMCPGateway(tools=[], responses={})
    events = await _run("研究问题", llm, mcp=gw)

    assert gw.names() == []
    assert llm.seen_tools[-1] == [SEARCH_WEB_TOOL]
    assert "工具补充资料（MCP" not in _synthesize_user_content(llm)
    assert llm.complete_calls == 3
    assert isinstance(events[-1], DoneEvent)


# ---- 启用：MCP schema 并入、工具事件、notes 到报告 -------------------------


async def test_mcp_tools_available_and_result_reaches_report():
    """MCP 启用：schema 并入 LLM 工具、调用走网关、notes 落 synthesize。"""
    gw = FakeMCPGateway(tools=[MEMORY_TOOL, PAPERS_TOOL], responses=MCP_RESPONSES)
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT])
    events = await _run("研究 RAG chunking", llm, mcp=gw)

    # schema 并入：最后一次 stream_chat 收到 search_web + 两个 MCP 工具
    names = _tool_names(llm.seen_tools[-1])
    assert names == ["search_web", "search_memory", "search_papers"]
    # 关键：ALL_TOOLS 本身没被就地污染（并入的是新列表）
    assert ALL_TOOLS == [SEARCH_WEB_TOOL]
    assert len(ALL_TOOLS) == 1

    # 工具事件转发到流（search_papers 走 MCP）
    assert any(
        isinstance(e, ToolCallEvent) and e.name == "search_papers" for e in events
    )
    assert any(
        isinstance(e, ToolResultEvent) and e.name == "search_papers" for e in events
    )
    assert gw.calls == [("search_papers", {"query": "RAG chunking"})]

    # notes 块进 synthesize：引导语 + [工具 search_papers] 文本
    content = _synthesize_user_content(llm)
    assert "工具补充资料（MCP，非网页搜索来源，仅供补充参考，不需要时可不引用）" in content
    assert "[工具 search_papers]" in content
    assert "arxiv.org/abs/2401.01234" in content  # notes 原文到达报告输入

    assert llm.complete_calls == 3  # MCP 工具不增加 complete() 调用


async def test_mcp_notes_accumulate_and_do_not_pollute_sources(tmp_path):
    """notes 跨研究轮累计；MCP 的 arXiv URL 不进 evidence → 不污染落库 sources。"""
    gw = FakeMCPGateway(tools=[MEMORY_TOOL, PAPERS_TOOL], responses=MCP_RESPONSES)
    store = create_memory_store(str(tmp_path / "memory.db"))
    llm = _fake(
        [PLANNER_JSON, EVAL_INSUFFICIENT, EVAL_SUFFICIENT, REPORT],
        script=SCRIPT_TWO_ROUNDS,
    )
    try:
        events = await _run(
            "研究 RAG chunking", llm, mcp=gw, memory=store, tmp_path=tmp_path
        )
        run = store.recent(1)[0]  # 必须在关库前读（close() 会释放连接）
    finally:
        store.close()

    # 两条不同的 MCP note（round1 memory + round2 papers）都在报告输入里
    content = _synthesize_user_content(llm)
    assert "[工具 search_memory]" in content
    assert "[工具 search_papers]" in content
    # 研究循环跑了两轮：planner + evaluate×2 + synthesize = 4 次非流式调用
    assert llm.complete_calls == 4
    assert isinstance(events[-1], DoneEvent)

    # sources 语义红线：只有 search_web 的 stub 来源，绝无 MCP 的 arXiv URL
    assert run["query"] == "研究 RAG chunking"
    urls = [s["url"] for s in run["sources"]]
    assert any("stub.example" in u for u in urls)
    assert not any("arxiv.org" in u for u in urls)
