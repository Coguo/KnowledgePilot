"""Agent 循环：用 Fake LLM 驱动多条路径，验证事件序列与消息回填。全程不联网。"""

from knowledge_pilot.agent.events import DoneEvent, TokenEvent, ToolCallEvent, ToolResultEvent
from knowledge_pilot.agent.loop import MAX_TOOL_ROUNDS, run_research
from knowledge_pilot.agent.tools import SEARCH_WEB_TOOL
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient


async def test_direct_answer_without_tools():
    llm = FakeChatClient(script=[(["这是直接回答。"], [])])
    search = StubSearchProvider()

    events = [e async for e in run_research("你好", llm=llm, search=search)]

    assert [type(e).__name__ for e in events] == ["TokenEvent", "DoneEvent"]
    text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert text == "这是直接回答。"
    assert events[-1].content == "这是直接回答。"

    assert llm.calls == 1
    assert llm.seen_tools[0] == [SEARCH_WEB_TOOL]


async def test_search_then_answer():
    llm = FakeChatClient(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "RAG chunking 策略"}'}]),
        (["根据搜索结果，常用的有 fixed-size、recursive、semantic。"], []),
    ])
    search = StubSearchProvider()

    events = [e async for e in run_research("研究 RAG chunking 策略", llm=llm, search=search)]

    # 事件序列：工具调用 → 工具结果 → 答案 token → 结束
    assert [type(e).__name__ for e in events] == [
        "ToolCallEvent", "ToolResultEvent", "TokenEvent", "DoneEvent",
    ]

    tool_call = events[0]
    assert isinstance(tool_call, ToolCallEvent)
    assert tool_call.name == "search_web"
    assert '"query"' in tool_call.arguments

    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert "fixed-size" in done.content

    # 第二轮 LLM 应收到回填的 tool message，且助手消息带完整 tool_calls
    assert llm.calls == 2
    messages_round2 = llm.seen_messages[1]
    roles = [m["role"] for m in messages_round2]
    assert roles == ["system", "user", "assistant", "tool"]
    assert messages_round2[2]["tool_calls"][0]["function"]["name"] == "search_web"
    assert messages_round2[2]["tool_calls"][0]["function"]["arguments"] == '{"query": "RAG chunking 策略"}'
    assert "stub" in messages_round2[3]["content"]
    assert messages_round2[3]["tool_call_id"] == "call_0"


async def test_max_tool_rounds_guard():
    """模型一直请求工具时，循环在 MAX_TOOL_ROUNDS 轮后兜底结束，不无限循环。"""
    llm = FakeChatClient(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "x"}'}]),  # 会一直重复这一条
    ])
    search = StubSearchProvider()

    events = [e async for e in run_research("问题", llm=llm, search=search)]

    assert llm.calls == MAX_TOOL_ROUNDS  # 恰好触发上限后停止
    assert isinstance(events[-1], DoneEvent)


async def test_unknown_tool_raises():
    """工具执行遇到未知工具名应报清晰错误（而非静默吞掉）。"""
    from knowledge_pilot.agent.tools import run_tool

    try:
        await run_tool("no_such_tool", {}, search=StubSearchProvider())
    except ValueError as e:
        assert "no_such_tool" in str(e)
    else:
        raise AssertionError("应当抛出 ValueError")
