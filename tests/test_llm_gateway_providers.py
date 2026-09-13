"""Model Gateway ↔ 真实 openai SDK（B 轨，需 openai）：provider 边界 + 逐字节 parity。

用 `httpx.MockTransport` 注入真实 `AsyncOpenAI`（不联网），验证：
- ProviderClient 的请求 body / usage 解析；
- 网关在真实 SDK 异常（429/400）上的重试与 fallback；
- 默认配置下 `ModelGateway` 与旧 `ChatClient` 的**外发 JSON 与返回内容逐字节相同**（铁律）。

openai 未装时整模块跳过（A 轨在无 openai 环境另有 test_llm_gateway.py 覆盖编排逻辑）。
"""

import asyncio
import json

import httpx
import pytest

pytest.importorskip("openai")

from openai import AsyncOpenAI, BadRequestError  # noqa: E402

from knowledge_pilot.config import Settings  # noqa: E402
from knowledge_pilot.llm.client import ChatClient  # noqa: E402
from knowledge_pilot.llm.gateway import ModelGateway  # noqa: E402
from knowledge_pilot.llm.providers import ProviderClient  # noqa: E402

_BASE_URL = "https://x/v1"
_MODEL = "deepseek-chat"


def _completion_json(text="你好", *, usage=True) -> dict:
    body = {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": _MODEL,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    return body


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
        chunk({}, choices=False),  # usage-only 收尾块
        "data: [DONE]\n\n",
    ]).encode("utf-8")


class Recorder:
    """MockTransport 处理器：记录每次请求 body，按脚本返回响应（耗尽后重复最后一个）。

    每次调用**克隆**一份响应返回——同一个 httpx.Response 被复用会被判定已消费。
    """

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


def _ok(text="你好") -> httpx.Response:
    return httpx.Response(200, json=_completion_json(text))


def _stream_ok() -> httpx.Response:
    return httpx.Response(
        200, content=_sse_body(), headers={"content-type": "text/event-stream"}
    )


def _error(code: int) -> httpx.Response:
    return httpx.Response(
        code,
        json={"error": {"message": f"HTTP {code}", "type": "invalid_request_error", "code": None}},
        headers={"content-type": "application/json"},
    )


def _provider(recorder: Recorder, **kwargs) -> tuple[ProviderClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    provider = ProviderClient(
        name="deepseek", model=_MODEL, base_url=_BASE_URL, api_key="sk-test",
        http_client=http, **kwargs,
    )
    return provider, http


def _run(recorder: Recorder, provider_kwargs=None, gateway_kwargs=None, *, kind="complete"):
    """跑一次 complete/stream，返回 (结果, recorder)；自动关闭注入的 httpx 客户端。"""
    provider, http = _provider(recorder, **(provider_kwargs or {}))
    gw = ModelGateway([provider], **(gateway_kwargs or {}))
    messages = [{"role": "user", "content": "hi"}]

    async def main():
        try:
            if kind == "complete":
                return await gw.complete(messages, max_tokens=64)
            return [c async for c in gw.stream_chat(messages, tools=[])]
        finally:
            await http.aclose()

    return asyncio.run(main())


# ---- ProviderClient 边界 -------------------------------------------------

def test_provider_complete_parses_usage_and_body():
    rec = Recorder([_ok("你好")])
    text = _run(rec)
    assert text == "你好"
    body = rec.bodies[0]
    assert body["model"] == _MODEL
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_tokens"] == 64
    assert "stream" not in body  # 非流式不带 stream


def test_provider_stream_yields_content_and_no_stream_options_by_default():
    rec = Recorder([_stream_ok()])
    chunks = _run(rec, kind="stream")
    assert [c.content_delta for c in chunks] == ["你", "好"]  # usage-only 块被跳过

    body = rec.bodies[0]
    assert body["stream"] is True
    assert "stream_options" not in body  # 默认不发（与 ChatClient 逐字节一致）


def test_provider_stream_sends_stream_options_when_log_usage():
    rec = Recorder([_stream_ok()])
    _run(rec, gateway_kwargs={"log_usage": True}, kind="stream")
    assert rec.bodies[0]["stream_options"] == {"include_usage": True}


def test_provider_timeout_is_passed_to_client():
    provider, http = _provider(Recorder([_ok()]), timeout=1.5)
    try:
        timeout = provider._ensure_client().timeout
        # openai 1.x 会把 timeout 归一化成 httpx.Timeout，3.x 原样保留 float——
        # 两种都算「透传成功」，断言不锁死 SDK 大版本。
        assert timeout == 1.5 or timeout == httpx.Timeout(1.5)
    finally:
        asyncio.run(http.aclose())


# ---- 真实 SDK 异常：重试 / 不重试 / fallback -----------------------------

def test_gateway_retries_429_then_succeeds():
    rec = Recorder([_error(429), _ok("恢复了")])
    # max_retries=0：关掉 SDK 自身重试，隔离验证「网关重试」。
    text = _run(
        rec,
        provider_kwargs={"max_retries": 0},
        gateway_kwargs={"retry_enabled": True, "max_attempts": 2, "backoff_seconds": 0.0},
    )
    assert text == "恢复了"
    assert rec.calls == 2  # 恰好一次重试


def test_gateway_does_not_retry_400():
    rec = Recorder([_error(400)])
    with pytest.raises(BadRequestError):
        _run(
            rec,
            provider_kwargs={"max_retries": 0},
            gateway_kwargs={"retry_enabled": True, "max_attempts": 3, "backoff_seconds": 0.0},
        )
    assert rec.calls == 1  # 400 不重试


def test_gateway_falls_back_to_second_provider():
    primary_rec = Recorder([_error(503)])
    backup_rec = Recorder([_ok("备份答案")])
    p1, http1 = _provider(primary_rec, max_retries=0)
    p2, http2 = _provider(backup_rec, max_retries=0)
    p2.name = "qwen"  # type: ignore[misc]
    gw = ModelGateway([p1, p2], retry_enabled=True, max_attempts=2, backoff_seconds=0.0)

    async def main():
        try:
            return await gw.complete([{"role": "user", "content": "hi"}])
        finally:
            await http1.aclose()
            await http2.aclose()

    assert asyncio.run(main()) == "备份答案"
    assert primary_rec.calls == 2  # primary 重试耗尽
    assert backup_rec.calls == 1  # 才退到 fallback


# ---- 逐字节 parity：默认 ModelGateway vs ChatClient（铁律）--------------

@pytest.mark.parametrize("tools", [[], None])
def test_default_gateway_matches_chatclient(monkeypatch, tools):
    """默认配置下网关必须与旧 ChatClient 外发完全相同的 body、返回相同内容/增量。"""
    messages = [{"role": "user", "content": "hi"}]

    # --- ChatClient 侧：把其内部构造的 AsyncOpenAI 换成走 MockTransport 的实例 ---
    chat_rec = Recorder([_ok("相同文本"), _stream_ok()])
    chat_http = httpx.AsyncClient(transport=httpx.MockTransport(chat_rec))
    monkeypatch.setattr(
        "knowledge_pilot.llm.client.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(**{**kwargs, "http_client": chat_http}),
    )
    settings = Settings(
        _env_file=None, deepseek_api_key="sk-test",
        deepseek_base_url=_BASE_URL, deepseek_model=_MODEL,
    )
    chat = ChatClient(settings)

    # --- Gateway 侧：默认（不重试/不 fallback/不记 usage）单 provider ---
    gw_rec = Recorder([_ok("相同文本"), _stream_ok()])
    provider, gw_http = _provider(gw_rec)
    gw = ModelGateway([provider])

    async def main():
        try:
            chat_done = await chat.complete(messages, max_tokens=64,
                                            response_format={"type": "json_object"})
            gw_done = await gw.complete(messages, max_tokens=64,
                                        response_format={"type": "json_object"})
            chat_stream = [c async for c in chat.stream_chat(messages, tools=tools)]
            gw_stream = [c async for c in gw.stream_chat(messages, tools=tools)]
            return chat_done, gw_done, chat_stream, gw_stream
        finally:
            await chat_http.aclose()
            await gw_http.aclose()

    chat_done, gw_done, chat_stream, gw_stream = asyncio.run(main())

    # 外发 JSON body 逐字节一致（非流式 + 流式）。
    assert chat_rec.bodies[0] == gw_rec.bodies[0]
    assert chat_rec.bodies[1] == gw_rec.bodies[1]
    # 返回内容 / 流式增量一致。
    assert chat_done == gw_done
    assert chat_stream == gw_stream
    # 单独锁死：默认流式请求不含 stream_options。
    assert "stream_options" not in gw_rec.bodies[1]
