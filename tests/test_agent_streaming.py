"""Phase 9 M1：报告流式输出 + 流内异常转 error 帧。

**这里的「默认路径 vs 测试路径」分叉是有意的，也是本文件存在的首要理由。**
生产客户端（`ChatClient` / `ModelGateway`）实现了 `stream_complete`，报告逐字到达；
而测试里的 `FakeChatClient` 与 `agent/eval/real.py::CountingChatClient` 都没有它，
`_report_text` 能力探测后自动退回 `complete` —— 这正是约 40 条既有断言
（`complete_calls` 计数、事件类型精确序列）能一行不改的原因。

代价是「测的不是跑的那条路」。故本文件用 `FakeStreamingClient` 显式覆盖生产路径，
再用 `test_graph_report_falls_back_without_streaming` 把降级路径钉死，防后人误删
探测逻辑（删了它，那 40 条断言会一起炸，但那次爆炸要等到改完才发现）。
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from knowledge_pilot.agent.events import DoneEvent, ErrorEvent, TokenEvent
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.api import main as api_main
from knowledge_pilot.api.main import ChatDeps, app, get_chat_deps
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient

PLANNER_JSON = '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}'
EVAL_SUFFICIENT = '{"sufficient": true, "reason": "资料足够", "gap": ""}'
REPORT = "# 研究报告\n这是最终报告。"
SCRIPT_DIRECT = [(["研究完成。"], [])]


class FakeStreamingClient(FakeChatClient):
    """在 FakeChatClient 上加 `stream_complete` —— 即生产路径的能力探测命中它。

    deltas：第 n 次 stream_complete 调用返回的增量列表（按调用序号消费，越界重复
    最后一条，与父类 complete_script 同口径）。
    """

    def __init__(self, script, deltas):
        super().__init__(script)
        self.deltas = deltas
        self.stream_complete_calls = 0
        self.seen_max_tokens: list = []

    async def stream_complete(self, messages, *, max_tokens=None, response_format=None):
        self.seen_messages.append(list(messages))
        self.seen_max_tokens.append(max_tokens)
        idx = min(self.stream_complete_calls, len(self.deltas) - 1)
        self.stream_complete_calls += 1
        for delta in self.deltas[idx]:
            yield delta


class _BoomClient:
    """stream_chat 立刻抛错：验证流内异常被转成 error 帧而不是静默截断。

    异常文本里**故意**放了一个形似密钥的串，用来验证 `_safe_error_text` 的脱敏。
    """

    model = "boom"
    SECRET = "sk-abcdefghijklmnopqrst"

    async def stream_chat(self, messages, tools=None):
        raise RuntimeError(f"上游连接失败（Authorization: Bearer {self.SECRET}）")
        yield  # pragma: no cover — 使本方法成为 async generator（此行永不执行）

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        raise RuntimeError("boom")


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


# ---- 流式路径（生产走这条） ----------------------------------------------


async def test_graph_report_streams_tokens_when_client_supports_it():
    """客户端有 stream_complete：报告增量逐个变 TokenEvent，拼接 == done.content。"""
    llm = FakeStreamingClient(script=SCRIPT_DIRECT, deltas=[[REPORT[:4], REPORT[4:]]])
    # 只有 planner + evaluate 走 complete；synthesize 改走 stream_complete。
    llm.complete_script = [PLANNER_JSON, EVAL_SUFFICIENT]

    events = await _run("研究问题", llm)

    assert [type(e).__name__ for e in events] == [
        "PlanEvent", "StatusEvent", "EvalEvent", "StatusEvent",
        "TokenEvent", "TokenEvent", "DoneEvent",
    ]
    tokens = [e.content for e in events if isinstance(e, TokenEvent)]
    assert tokens == [REPORT[:4], REPORT[4:]]

    done = events[-1]
    assert done.content == "".join(tokens) == REPORT
    assert llm.stream_complete_calls == 1
    assert llm.seen_max_tokens == [4096]  # 与旧 llm.complete(prompt, max_tokens=4096) 同参
    assert llm.complete_calls == 2  # 报告不再经 complete


async def test_streaming_report_keeps_done_event_for_runner_fallback():
    """DoneEvent 仍由 synthesize 发出（`_drive` 的 saw_done 与 checkpoint 兜底不变）。"""
    llm = FakeStreamingClient(script=SCRIPT_DIRECT, deltas=[["A", "B", "C"]])
    llm.complete_script = [PLANNER_JSON, EVAL_SUFFICIENT]

    events = await _run("研究问题", llm)

    done = [e for e in events if isinstance(e, DoneEvent)]
    assert len(done) == 1  # 不是「没收到 done 于是兜底补发」的第二个
    assert done[0].content == "ABC"


async def test_streaming_report_skips_empty_deltas():
    """空增量不推 TokenEvent（否则前端会收到无意义帧）。"""
    llm = FakeStreamingClient(script=SCRIPT_DIRECT, deltas=[["前半", "", "后半"]])
    llm.complete_script = [PLANNER_JSON, EVAL_SUFFICIENT]

    events = await _run("研究问题", llm)

    tokens = [e.content for e in events if isinstance(e, TokenEvent)]
    assert tokens == ["前半", "后半"]
    assert events[-1].content == "前半后半"


# ---- 降级路径（既有测试走这条，必须钉死） --------------------------------


async def test_graph_report_falls_back_without_streaming():
    """客户端没有 stream_complete → 退回 complete，**零 TokenEvent**、调用计数不变。

    这条断言是「约 40 条既有断言不用改」的直接依据：探测逻辑一旦被删或改成无条件
    走流式，这里立刻红，而不是等 test_api / test_agent_graph 大面积炸才发现。
    """
    llm = FakeChatClient(script=SCRIPT_DIRECT)
    llm.complete_script = [PLANNER_JSON, EVAL_SUFFICIENT, REPORT]

    events = await _run("研究问题", llm)

    assert not [e for e in events if isinstance(e, TokenEvent)]
    assert events[-1].content == REPORT
    assert llm.complete_calls == 3  # planner + evaluate + synthesize


# ---- 流内异常 → error 帧 -------------------------------------------------


def _override_deps(llm):
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider()
    )


async def _collect_frames(message="你好"):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        async with client.stream("POST", "/api/chat", json={"message": message}) as resp:
            assert resp.status_code == 200
            frames = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    return frames


async def test_stream_exception_becomes_error_frame(monkeypatch):
    """流内异常 → error 帧 + [DONE]，而不是 200 + 截断 body（前端原本什么都不显示）。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    _override_deps(_BoomClient())
    try:
        frames = await _collect_frames()
    finally:
        app.dependency_overrides.clear()

    assert frames[-1] == "[DONE]"  # 收尾帧仍在（前端据此结束读取）
    assert len(frames) == 2
    assert frames[0]["type"] == "error"
    assert "RuntimeError" in frames[0]["message"]


async def test_error_frame_redacts_key_like_text(monkeypatch):
    """异常文本里的密钥形态串必须脱敏后才回给浏览器。"""
    monkeypatch.setattr(api_main.settings, "agent_mode", "loop")
    _override_deps(_BoomClient())
    try:
        frames = await _collect_frames()
    finally:
        app.dependency_overrides.clear()

    message = frames[0]["message"]
    assert _BoomClient.SECRET not in message
    assert "sk-***" in message


async def test_chat_does_not_swallow_cancellation():
    """CancelledError 继承 BaseException，不应被 except Exception 吞掉。"""
    import asyncio

    assert not issubclass(asyncio.CancelledError, Exception)


def test_safe_error_text_truncates_and_flattens():
    """超长异常压成一行并截断（避免把整个堆栈塞进 SSE 帧）。"""
    text = api_main._safe_error_text(RuntimeError("第一行\n第二行" + "x" * 500))
    assert "\n" not in text
    assert len(text) == 301  # 300 + 省略号
    assert text.endswith("…")


def test_sse_frame_maps_error_event():
    from knowledge_pilot.api.main import _sse_frame

    frame = _sse_frame(ErrorEvent(message="出错了"))
    assert json.loads(frame[len("data: "):]) == {"type": "error", "message": "出错了"}
