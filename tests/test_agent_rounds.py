"""Phase 9 M2：轮数上限配置化 + 请求级覆盖。

用户诉求原文是「输出查询轮数需要一定程度的限制，默认每次上限为 10 次；如果需要更精准
的查询，可以保留相应的接口方式」，随后确认为**数值暂不变、只把调整入口做出来**：
- 默认值仍是研究-评估 3 轮、工具 4 轮（`.env` 可调）；
- `/api/chat` 请求体可带 max_iterations / max_tool_rounds 做单次精准查询；
- 请求值被钳到 `AGENT_ROUNDS_HARD_CAP`（默认 10）。
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from knowledge_pilot.agent.events import DoneEvent, ToolCallEvent
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.agent.loop import MAX_TOOL_ROUNDS, run_research
from knowledge_pilot.api import main as api_main
from knowledge_pilot.api.main import ChatDeps, app, get_chat_deps
from knowledge_pilot.config import Settings, clamp_rounds
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient

# 模型每轮都要工具、从不给最终答案——用来把「轮次上限」逼到生效。
ALWAYS_SEARCH = [([], [{"name": "search_web", "arguments": '{"query": "x"}'}])]
PLANNER_JSON = '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}'
EVAL_SUFFICIENT = '{"sufficient": true, "reason": "够", "gap": ""}'


# ---- clamp_rounds：纯函数边界 -------------------------------------------


def test_clamp_rounds_none_falls_back_to_default():
    assert clamp_rounds(None, 3, 10) == 3
    assert clamp_rounds(None, 4, 10) == 4


def test_clamp_rounds_clamps_into_range():
    assert clamp_rounds(7, 3, 10) == 7  # 区间内原样
    assert clamp_rounds(50, 3, 10) == 10  # 超上限 → 钳住
    assert clamp_rounds(0, 3, 10) == 1  # 0/负数 → 至少 1 轮
    assert clamp_rounds(-5, 3, 10) == 1


def test_clamp_rounds_accepts_numeric_strings():
    """请求体里 `"max_iterations": "5"`（前端有时就这么发）也该能用。"""
    assert clamp_rounds("5", 3, 10) == 5


# ---- 配置项默认值（铁律：默认路径行为与旧版一致） ------------------------


def test_rounds_defaults_unchanged():
    s = Settings(_env_file=None)
    assert s.agent_max_iterations == 3  # 与 Phase 3 相同
    assert s.agent_max_tool_rounds == 4  # 与 loop.MAX_TOOL_ROUNDS 相同
    assert s.agent_rounds_hard_cap == 10


def test_loop_constant_still_matches_config_default():
    """`loop.MAX_TOOL_ROUNDS` 仍是默认值来源，两个数不能漂移。"""
    assert MAX_TOOL_ROUNDS == Settings(_env_file=None).agent_max_tool_rounds


def test_rounds_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TOOL_ROUNDS", "6")
    monkeypatch.setenv("AGENT_ROUNDS_HARD_CAP", "12")
    s = Settings(_env_file=None)
    assert s.agent_max_tool_rounds == 6
    assert s.agent_rounds_hard_cap == 12


# ---- run_research 的工具轮次上限 ----------------------------------------


async def _drain(agen):
    return [e async for e in agen]


async def test_loop_defaults_to_constant_when_not_overridden():
    """不传 max_tool_rounds → 用 MAX_TOOL_ROUNDS（与 Phase 0-8 逐字节一致）。"""
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    events = await _drain(
        run_research("q", llm=llm, search=StubSearchProvider())
    )
    assert llm.calls == MAX_TOOL_ROUNDS  # 恰好 4 轮 LLM 调用
    assert isinstance(events[-1], DoneEvent)


async def test_loop_honours_explicit_max_tool_rounds():
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    await _drain(
        run_research("q", llm=llm, search=StubSearchProvider(), max_tool_rounds=2)
    )
    assert llm.calls == 2


async def test_loop_max_tool_rounds_one_means_single_call():
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    events = await _drain(
        run_research("q", llm=llm, search=StubSearchProvider(), max_tool_rounds=1)
    )
    assert llm.calls == 1
    assert not [e for e in events if isinstance(e, ToolCallEvent)]  # 首轮就兜底结束


# ---- run_research_graph 透传到 research 节点 ----------------------------


async def test_graph_passes_max_tool_rounds_to_research_node():
    """graph 模式下每轮研究内的工具轮次同样受控（经 partial 注入，不进 ResearchState）。"""
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    llm.complete_script = [PLANNER_JSON, EVAL_SUFFICIENT, "# 报告"]

    events = await _drain(
        run_research_graph(
            "q",
            llm=llm,
            search=StubSearchProvider(),
            max_iterations=1,
            max_tool_rounds=2,
        )
    )

    # planner(complete) + evaluate(complete) + 2 轮工具(stream_chat)
    assert llm.calls == 2
    assert llm.complete_calls == 3
    assert events[-1].content == "# 报告"


# ---- /api/chat 的请求级覆盖 ---------------------------------------------


def _client_deps(llm):
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider()
    )


async def _post(payload):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        async with client.stream("POST", "/api/chat", json=payload) as resp:
            frames = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    return frames


async def test_chat_request_overrides_tool_rounds(monkeypatch):
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    _client_deps(llm)
    try:
        frames = await _post({"message": "精确查一次", "max_tool_rounds": 2})
    finally:
        app.dependency_overrides.clear()

    assert llm.calls == 2
    assert frames[-1] == "[DONE]"


async def test_chat_request_value_is_clamped_to_hard_cap(monkeypatch):
    """请求端想开到 999 → 被钳到硬上限，服务不会被拖死。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    monkeypatch.setattr(api_main.settings, "agent_rounds_hard_cap", 3)
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    _client_deps(llm)
    try:
        await _post({"message": "贪心", "max_tool_rounds": 999})
    finally:
        app.dependency_overrides.clear()

    assert llm.calls == 3  # 不是 999


async def test_chat_without_override_uses_env_value(monkeypatch):
    """老请求体（只有 message）行为不变；.env 的值仍然生效。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    _client_deps(llm)
    try:
        await _post({"message": "只发 message"})
    finally:
        app.dependency_overrides.clear()

    assert llm.calls == MAX_TOOL_ROUNDS  # 未覆盖 → 默认 4


async def test_chat_request_overrides_max_iterations_in_graph(monkeypatch):
    monkeypatch.setattr(api_main.settings, "agent_mode", "graph")
    llm = FakeChatClient(script=[(["研究完成。"], [])])
    llm.complete_script = [
        PLANNER_JSON,
        '{"sufficient": false, "reason": "不够", "gap": "再找"}',
        "# 报告",
    ]
    _client_deps(llm)
    try:
        frames = await _post({"message": "只跑一轮", "max_iterations": 1})
    finally:
        app.dependency_overrides.clear()

    evals = [f for f in frames if f != "[DONE]" and f.get("type") == "eval"]
    assert len(evals) == 1  # 到上限即收尾，不回到 research
    assert frames[-1] == "[DONE]"


@pytest.mark.parametrize("bad", [0, -3])
async def test_chat_freezes_invalid_round_values(monkeypatch, bad):
    """0 / 负数被钳到 1（至少跑一轮），而不是直接崩或不跑。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    llm = FakeChatClient(script=ALWAYS_SEARCH)
    _client_deps(llm)
    try:
        await _post({"message": "x", "max_tool_rounds": bad})
    finally:
        app.dependency_overrides.clear()

    assert llm.calls == 1
