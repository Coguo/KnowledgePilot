"""Phase 9 M1：`stream_complete`（`complete` 的流式孪生）在真实 openai SDK 上的行为。

B 轨：用 `httpx.MockTransport` 注入真实 `AsyncOpenAI`（不联网）。A 轨（无 openai 环境）
由 `test_agent_streaming.py` 的 `FakeStreamingClient` 覆盖编排层。

三件必须锁死的事：
1. `ChatClient.stream_complete` 的外发 body 与 `ChatClient.complete` **只差 `stream`**；
2. 默认配置下 `ModelGateway.stream_complete` 与 `ChatClient.stream_complete` 逐字节一致；
3. 重试**只在首字节之前**——流已吐字后出错绝不重来（否则用户看到重复片段）。
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("openai")

from openai import AsyncOpenAI  # noqa: E402

from knowledge_pilot.config import Settings  # noqa: E402
from knowledge_pilot.llm.client import ChatClient  # noqa: E402
from knowledge_pilot.llm.gateway import ModelGateway  # noqa: E402
from knowledge_pilot.llm.providers import ProviderClient  # noqa: E402

_BASE_URL = "https://x/v1"
_MODEL = "deepseek-chat"
_MESSAGES = [{"role": "user", "content": "hi"}]


def _sse_body() -> bytes:
    """最小 SSE 流：两个内容增量 + 一个 usage-only 收尾块 + [DONE]。"""
    def chunk(delta: dict, choices=True) -> str:
        payload = {
            "id": "cmpl-1", "object": "chat.completion.chunk", "created": 0,
            "model": _MODEL,
            "choices": ([{"index": 0, "delta": delta, "finish_reason": None}] if choices else []),
        }
        if not choices:
            payload["usage"] = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        return f"data: {json.dumps(payload)}\n\n"

    return "".join([
        chunk({"content": "你"}),
        chunk({"content": "好"}),
        chunk({}, choices=False),
        "data: [DONE]\n\n",
    ]).encode("utf-8")


class Recorder:
    """MockTransport 处理器：记录每次请求 body，按脚本返回响应（耗尽后重复最后一个）。"""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.bodies: list[dict] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content) if request.content else {})
        idx = min(len(self.bodies) - 1, len(self._responses) - 1)
        template = self._responses[idx]
        return httpx.Response(
            template.status_code, content=template.content, headers=template.headers
        )

    @property
    def calls(self) -> int:
        return len(self.bodies)


def _stream_ok() -> httpx.Response:
    return httpx.Response(
        200, content=_sse_body(), headers={"content-type": "text/event-stream"}
    )


def _completion_ok(text="你好") -> httpx.Response:
    return httpx.Response(200, json={
        "id": "cmpl-1", "object": "chat.completion", "created": 0, "model": _MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    })


def _error(code: int) -> httpx.Response:
    return httpx.Response(
        code,
        json={"error": {"message": f"HTTP {code}", "type": "server_error", "code": None}},
        headers={"content-type": "application/json"},
    )


def _provider(recorder: Recorder, **kwargs) -> tuple[ProviderClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    provider = ProviderClient(
        name="deepseek", model=_MODEL, base_url=_BASE_URL, api_key="sk-test",
        http_client=http, **kwargs,
    )
    return provider, http


def _chat_client(monkeypatch, recorder: Recorder) -> tuple[ChatClient, httpx.AsyncClient]:
    """把 ChatClient 内部的 AsyncOpenAI 换成走 MockTransport 的实例。"""
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(
        "knowledge_pilot.llm.client.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(**{**kwargs, "http_client": http}),
    )
    settings = Settings(
        _env_file=None, deepseek_api_key="sk-test",
        deepseek_base_url=_BASE_URL, deepseek_model=_MODEL,
    )
    return ChatClient(settings), http


# ---- ChatClient.stream_complete -----------------------------------------


def test_chatclient_stream_complete_yields_text_deltas(monkeypatch):
    chat, http = _chat_client(monkeypatch, Recorder([_stream_ok()]))

    async def main():
        try:
            return [d async for d in chat.stream_complete(_MESSAGES, max_tokens=4096)]
        finally:
            await http.aclose()

    assert asyncio.run(main()) == ["你", "好"]  # usage-only 收尾块被跳过


def test_chatclient_stream_complete_body_matches_complete_except_stream(monkeypatch):
    """孪生契约：外发 body 与 `complete()` 只差 `stream`（FR 报告正文用同一个 prompt）。"""
    rec = Recorder([_completion_ok(), _stream_ok()])
    chat, http = _chat_client(monkeypatch, rec)

    async def main():
        try:
            await chat.complete(_MESSAGES, max_tokens=4096)
            _ = [d async for d in chat.stream_complete(_MESSAGES, max_tokens=4096)]
        finally:
            await http.aclose()

    asyncio.run(main())

    body_complete, body_stream = rec.bodies
    assert body_stream.pop("stream") is True
    assert "stream" not in body_complete
    assert body_stream == body_complete


# ---- ModelGateway.stream_complete：默认配置与 ChatClient 逐字节一致 -------


def test_default_gateway_stream_complete_matches_chatclient(monkeypatch):
    """铁律：默认网关（不重试/不 fallback/不记 usage）与旧 ChatClient 外发完全一致。"""
    chat_rec = Recorder([_stream_ok()])
    chat, chat_http = _chat_client(monkeypatch, chat_rec)

    gw_rec = Recorder([_stream_ok()])
    provider, gw_http = _provider(gw_rec)
    gw = ModelGateway([provider])

    async def main():
        try:
            chat_out = [d async for d in chat.stream_complete(_MESSAGES, max_tokens=4096)]
            gw_out = [d async for d in gw.stream_complete(_MESSAGES, max_tokens=4096)]
            return chat_out, gw_out
        finally:
            await chat_http.aclose()
            await gw_http.aclose()

    chat_out, gw_out = asyncio.run(main())

    assert chat_out == gw_out == ["你", "好"]
    # 两个类在 body 上有两处构造性差异（都早于 Phase 9，各有理由，本次不动）：
    #   - ProviderClient.open_stream 无条件发 `tools`（Phase 8 的 stream_chat parity 要求）；
    #   - ChatClient.stream_complete 无条件发 `response_format`（它是 complete 的孪生）。
    # 两者都是 null、服务端一律忽略。**逐字节**相等锁在同一类的两个方法上
    # （见 test_chatclient_stream_complete_body_matches_complete_except_stream）。
    for body in (chat_rec.bodies[0], gw_rec.bodies[0]):
        body.pop("tools", None)
        body.pop("response_format", None)
    assert chat_rec.bodies[0] == gw_rec.bodies[0]
    assert chat_rec.bodies[0]["max_tokens"] == 4096
    assert "stream_options" not in gw_rec.bodies[0]  # 默认不发（同 stream_chat）


def test_gateway_stream_complete_omits_max_tokens_when_none():
    """max_tokens=None → 键**不出现**在 body 里（不是 `null`）。

    `open_stream` 的既有调用方（`stream_chat`）从不传 max_tokens，靠这条保持
    「默认外发 JSON 与 Phase 8 逐字节一致」——多一个 `"max_tokens": null` 就是破约。
    """
    rec = Recorder([_stream_ok()])
    provider, http = _provider(rec)
    gw = ModelGateway([provider])

    async def main():
        try:
            return [d async for d in gw.stream_complete(_MESSAGES)]
        finally:
            await http.aclose()

    assert asyncio.run(main()) == ["你", "好"]
    assert "max_tokens" not in rec.bodies[0]


# ---- 重试语义：只在首字节之前 --------------------------------------------


def test_gateway_stream_complete_retries_open_failure():
    """打开阶段失败（还没吐字）→ 可重试，用户只看到最终成功的结果。"""
    rec = Recorder([_error(503), _stream_ok()])
    provider, http = _provider(rec, max_retries=0)
    gw = ModelGateway([provider], retry_enabled=True, max_attempts=2, backoff_seconds=0.0)

    async def main():
        try:
            return [d async for d in gw.stream_complete(_MESSAGES, max_tokens=16)]
        finally:
            await http.aclose()

    assert asyncio.run(main()) == ["你", "好"]
    assert rec.calls == 2  # 恰好一次重试


class _MidStreamBoom(Exception):
    """首字节之后才发生的错误（真实场景：连接在流中途断掉）。"""


class _FakeStream:
    """鸭子类型假流：先吐若干块，然后抛错。"""

    def __init__(self, texts: list[str], error: Exception | None = None) -> None:
        self._texts = texts
        self._error = error

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for text in self._texts:
            delta = SimpleNamespace(content=text, tool_calls=None)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)
        if self._error is not None:
            raise self._error


class _FakeProvider:
    """只实现网关需要的鸭子接口（name/model + open_stream）。"""

    def __init__(self, stream: _FakeStream) -> None:
        self.name = "flaky"
        self.model = _MODEL
        self._stream = stream
        self.open_calls = 0

    async def open_stream(self, messages, tools=None, *, include_usage=False, max_tokens=None):
        self.open_calls += 1
        self.last_max_tokens = max_tokens
        return self._stream


def test_gateway_stream_complete_does_not_retry_after_first_byte():
    """流已吐字后出错：**不重试**、原样上抛——重来一次会让用户看到重复片段。

    与 `stream_chat` 的语义一致（Phase 8 铁律「首字节后绝不重试」）；报告以 4096 token
    上限生成，重复片段会直接进最终文档，代价比一次失败大得多。
    """
    provider = _FakeProvider(_FakeStream(["前半段"], _MidStreamBoom("流中途断开")))
    gw = ModelGateway([provider], retry_enabled=True, max_attempts=3, backoff_seconds=0.0)

    seen: list[str] = []

    async def main():
        async for delta in gw.stream_complete(_MESSAGES, max_tokens=4096):
            seen.append(delta)

    with pytest.raises(_MidStreamBoom):
        asyncio.run(main())

    assert seen == ["前半段"]  # 已到达的字保留（前端能显示），但不重来
    assert provider.open_calls == 1  # 三次机会一次都没用
    assert provider.last_max_tokens == 4096
