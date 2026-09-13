"""Model Gateway 编排逻辑（Phase 8）：重试 / fallback / 流式首字节语义 / usage 记录。

全程不 import openai —— 用**鸭子类型假 Provider** 驱动网关；真实 ProviderClient 与
逐字节 parity 在 B 轨（tests/test_llm_gateway_providers.py）验。

测试用同步函数包 `asyncio.run`（而非 async def），使这些用例在**未装 pytest-asyncio**
的环境（如离线沙箱）也能真正执行，而不是被静默跳过。
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from knowledge_pilot.llm.gateway import ModelGateway
from knowledge_pilot.llm.protocol import StreamChunk


class _HttpStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


# ---- 假 openai 流式块（只具备网关读取的字段）------------------------------

class _Delta:
    def __init__(self, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta) -> None:
        self.delta = delta


class _Chunk:
    def __init__(self, choices, usage=None) -> None:
        self.choices = choices
        self.usage = usage


class _Stream:
    """异步可迭代的假流：元素为 chunk，或「抛出的异常」（模拟流中途断线）。"""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        async def _gen():
            for item in self._items:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return _gen()


# ---- 假 Provider（鸭子：name/model + complete/open_stream）----------------

class FakeProvider:
    def __init__(self, name="p1", model="m1", *, complete_outcomes=None, open_outcomes=None, chunks=None):
        self.name = name
        self.model = model
        self.complete_calls = 0
        self.open_calls = 0
        self.last_include_usage = None
        # 每项：("ok", text, usage_or_None) 或 ("err", exc)；耗尽后默认返回 ("ok","ok",None)
        self._complete_outcomes = list(complete_outcomes or [])
        self._open_outcomes = list(open_outcomes or [])
        self._chunks = list(chunks or [])

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        self.complete_calls += 1
        outcome = self._complete_outcomes.pop(0) if self._complete_outcomes else ("ok", "ok", None)
        if outcome[0] == "err":
            raise outcome[1]
        return outcome[1], outcome[2]

    async def open_stream(self, messages, tools=None, *, include_usage=False):
        self.open_calls += 1
        self.last_include_usage = include_usage
        outcome = self._open_outcomes.pop(0) if self._open_outcomes else ("ok",)
        if outcome[0] == "err":
            raise outcome[1]
        return _Stream(self._chunks)


def _gateway(providers, **kwargs):
    kwargs.setdefault("backoff_seconds", 0.0)  # 退避设为 0 → 测试不真等待
    return ModelGateway(providers, **kwargs)


def _run(coro):
    return asyncio.run(coro)


# ---- 构造 / model -------------------------------------------------------

def test_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        ModelGateway([])


def test_model_is_primary_provider_model():
    gw = _gateway([FakeProvider(name="deepseek", model="deepseek-chat"),
                   FakeProvider(name="qwen", model="qwen-plus")])
    assert gw.model == "deepseek-chat"


# ---- complete：默认 / 重试 / 非瞬时 / fallback ---------------------------

def test_complete_default_single_call_no_retry():
    p = FakeProvider(complete_outcomes=[("ok", "答案", None)])
    gw = _gateway([p])
    assert _run(gw.complete([{"role": "user", "content": "hi"}])) == "答案"
    assert p.complete_calls == 1


def test_complete_transient_raises_when_retry_disabled():
    p = FakeProvider(complete_outcomes=[("err", _HttpStatusError(503))])
    gw = _gateway([p])  # retry_enabled 默认 False
    with pytest.raises(_HttpStatusError):
        _run(gw.complete([{"role": "user", "content": "hi"}]))
    assert p.complete_calls == 1  # 只调一次


def test_complete_retries_transient_then_succeeds():
    p = FakeProvider(complete_outcomes=[
        ("err", _HttpStatusError(429)),
        ("err", httpx.ConnectError("boom")),
        ("ok", "最终答案", None),
    ])
    gw = _gateway([p], retry_enabled=True, max_attempts=3)
    assert _run(gw.complete([{"role": "user", "content": "hi"}])) == "最终答案"
    assert p.complete_calls == 3


def test_complete_raises_after_retries_exhausted():
    p = FakeProvider(complete_outcomes=[("err", _HttpStatusError(503))] * 3)
    gw = _gateway([p], retry_enabled=True, max_attempts=2)
    with pytest.raises(_HttpStatusError):
        _run(gw.complete([{"role": "user", "content": "hi"}]))
    assert p.complete_calls == 2  # 恰好 max_attempts 次，不多调


def test_complete_non_transient_never_retried():
    p = FakeProvider(complete_outcomes=[("err", _HttpStatusError(400))])
    gw = _gateway([p], retry_enabled=True, max_attempts=3)
    with pytest.raises(_HttpStatusError):
        _run(gw.complete([{"role": "user", "content": "hi"}]))
    assert p.complete_calls == 1  # 400 不该重试


def test_complete_falls_back_to_second_provider():
    p1 = FakeProvider(name="deepseek", complete_outcomes=[("err", _HttpStatusError(503))] * 2)
    p2 = FakeProvider(name="qwen", complete_outcomes=[("ok", "备份答案", None)])
    gw = _gateway([p1, p2], retry_enabled=True, max_attempts=2)
    assert _run(gw.complete([{"role": "user", "content": "hi"}])) == "备份答案"
    assert p1.complete_calls == 2  # primary 重试耗尽
    assert p2.complete_calls == 1  # 才退到 fallback


def test_complete_auth_error_does_not_fall_back():
    p1 = FakeProvider(name="deepseek", complete_outcomes=[("err", _HttpStatusError(401))])
    p2 = FakeProvider(name="qwen", complete_outcomes=[("ok", "不该被调用", None)])
    gw = _gateway([p1, p2], retry_enabled=True, max_attempts=3, )
    with pytest.raises(_HttpStatusError):
        _run(gw.complete([{"role": "user", "content": "hi"}]))
    assert p1.complete_calls == 1
    assert p2.complete_calls == 0  # 401 不 fallback


def test_complete_fallback_single_attempt_when_retry_disabled():
    """重试关但 fallback 开：primary 一次性失败即顺延下一家（不重试 primary）。"""
    p1 = FakeProvider(name="deepseek", complete_outcomes=[("err", _HttpStatusError(500))])
    p2 = FakeProvider(name="qwen", complete_outcomes=[("ok", "备用", None)])
    gw = _gateway([p1, p2])  # retry_enabled 默认 False
    assert _run(gw.complete([{"role": "user", "content": "hi"}])) == "备用"
    assert p1.complete_calls == 1  # 只试一次
    assert p2.complete_calls == 1


# ---- 流式：内容 / tool_call 透传 ----------------------------------------

def test_stream_chat_maps_content_and_tool_call_deltas():
    fn = SimpleNamespace(name="search_memory", arguments='{"q":"x"}')
    tc = SimpleNamespace(index=0, id="call_1", function=fn)
    p = FakeProvider(chunks=[
        _Chunk([_Choice(_Delta(content="你好"))]),
        _Chunk([_Choice(_Delta(tool_calls=[tc]))]),
    ])
    gw = _gateway([p])

    async def collect():
        return [c async for c in gw.stream_chat([{"role": "user", "content": "hi"}], tools=[])]

    out = _run(collect())
    assert out == [
        StreamChunk(content_delta="你好"),
        StreamChunk(tool_call_delta={
            "index": 0, "id": "call_1", "name": "search_memory", "arguments": '{"q":"x"}',
        }),
    ]


def test_stream_chat_skips_empty_delta_chunk():
    p = FakeProvider(chunks=[_Chunk([_Choice(_Delta())])])  # delta 全空 → 不产出
    gw = _gateway([p])

    async def collect():
        return [c async for c in gw.stream_chat([{"role": "user", "content": "hi"}])]

    assert _run(collect()) == []


# ---- 流式：仅首字节前重试 -----------------------------------------------

def test_stream_retries_before_first_byte():
    p = FakeProvider(
        open_outcomes=[("err", httpx.ConnectError("boom"))],  # 首字节前失败
        chunks=[_Chunk([_Choice(_Delta(content="成功"))])],
    )
    gw = _gateway([p], retry_enabled=True, max_attempts=2)

    async def collect():
        return [c async for c in gw.stream_chat([{"role": "user", "content": "hi"}])]

    assert _run(collect()) == [StreamChunk(content_delta="成功")]
    assert p.open_calls == 2  # 打开阶段重试了一次


def test_stream_non_transient_open_error_not_retried():
    p = FakeProvider(open_outcomes=[("err", _HttpStatusError(400))])
    gw = _gateway([p], retry_enabled=True, max_attempts=3)

    async def collect():
        return [c async for c in gw.stream_chat([{"role": "user", "content": "hi"}])]

    with pytest.raises(_HttpStatusError):
        _run(collect())
    assert p.open_calls == 1


def test_stream_mid_stream_error_not_retried():
    """首字节后断线绝不重试（防重复产出已流出的片段）。"""
    p = FakeProvider(
        chunks=[_Chunk([_Choice(_Delta(content="前半"))]), httpx.ConnectError("断线")],
        open_outcomes=[("ok",)],
    )
    gw = _gateway([p], retry_enabled=True, max_attempts=3)

    async def collect():
        got = []
        async for c in gw.stream_chat([{"role": "user", "content": "hi"}]):
            got.append(c)
        return got

    with pytest.raises(httpx.ConnectError):
        _run(collect())
    assert p.open_calls == 1  # 未重新打开


def test_stream_falls_back_before_first_byte():
    p1 = FakeProvider(name="deepseek", open_outcomes=[("err", _HttpStatusError(503))])
    p2 = FakeProvider(name="qwen", chunks=[_Chunk([_Choice(_Delta(content="备用流"))])])
    gw = _gateway([p1, p2], retry_enabled=True, max_attempts=1)

    async def collect():
        return [c async for c in gw.stream_chat([{"role": "user", "content": "hi"}])]

    assert _run(collect()) == [StreamChunk(content_delta="备用流")]
    assert p1.open_calls == 1
    assert p2.open_calls == 1


# ---- usage 记录 ---------------------------------------------------------

def test_no_usage_logging_by_default():
    p = FakeProvider(complete_outcomes=[
        ("ok", "x", SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)),
    ], chunks=[_Chunk([_Choice(_Delta(content="a"))])])
    records: list[dict] = []
    gw = _gateway([p], usage_logger=records.append)
    _run(gw.complete([{"role": "user", "content": "hi"}]))

    async def collect():
        return [c async for c in gw.stream_chat([{"role": "user", "content": "hi"}])]

    _run(collect())
    assert records == []
    assert p.last_include_usage is False  # 默认不发 stream_options


def test_complete_usage_logged_when_enabled():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    p = FakeProvider(complete_outcomes=[("ok", "x", usage)])
    records: list[dict] = []
    gw = _gateway([p], log_usage=True, usage_logger=records.append)
    _run(gw.complete([{"role": "user", "content": "hi"}]))
    assert records == [{
        "kind": "complete", "provider": "p1", "model": "m1",
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    }]


def test_stream_usage_only_chunk_recorded_not_yielded():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    p = FakeProvider(chunks=[
        _Chunk([_Choice(_Delta(content="你"))]),
        _Chunk([], usage=usage),  # usage-only 收尾块（无 choices）
    ])
    records: list[dict] = []
    gw = _gateway([p], log_usage=True, usage_logger=records.append)

    async def collect():
        return [c async for c in gw.stream_chat([{"role": "user", "content": "hi"}])]

    out = _run(collect())
    assert out == [StreamChunk(content_delta="你")]  # usage 块不产出
    assert p.last_include_usage is True  # 开日志才发 include_usage
    assert records == [{
        "kind": "stream", "provider": "p1", "model": "m1",
        "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3,
    }]
