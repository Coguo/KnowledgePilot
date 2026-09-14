"""LLM 接口定义：流式增量类型 + 客户端 Protocol。纯 stdlib/typing，零重依赖。

与 `client.py`（生产 `ChatClient`，顶层 import openai）分开的原因：Model Gateway
（Phase 8）与其测试要能在无 openai 的环境 import 本文件来标注/构造协议对象；
`client.py` 从本文件 re-export `StreamChunk` / `LLMClient`，因此全仓既有
`from knowledge_pilot.llm.client import LLMClient` 的导入路径完全不变。
"""

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol


@dataclass
class StreamChunk:
    """一次流式回调携带的增量。content 与 tool_call 至少有一个非空。"""

    content_delta: str | None = None
    # tool_call 增量：{"index", "id"?, "name"?, "arguments"?}，字段可为 None，
    # 由上层按 index 累加拼出完整 tool_call。
    tool_call_delta: dict[str, Any] | None = None


class LLMClient(Protocol):
    """LLM 客户端接口（测试时用 Fake 实现注入）。

    实现方（ChatClient / ModelGateway / 各 Fake）只需满足这三个成员。
    """

    model: str

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        ...

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        extra_body: dict | None = None,
    ) -> str:
        """非流式补全：给定消息返回完整文本（Query Rewrite / Planner / Evaluate 用）。

        response_format 透传给 OpenAI 兼容接口（如 {"type": "json_object"}）。

        `extra_body` 是 **provider 方言的逃生口**，原样合并进请求体（第七轮加入）。
        它存在的原因是有些开关不在 OpenAI 兼容层的标准参数里——比如关掉推理模型的
        thinking（见 `providers.py::THINKING_OFF`），而我们的 `max_tokens` 会被推理
        和正文一起吃掉。方言常量定义在**说 SDK 那门语言的** `providers.py`，本协议
        不必认识任何一个具体方言。

        `None`（默认）→ 请求体里**一个字节都不多**：这条与 `max_tokens` 的既有约定
        同形，是 `ModelGateway` ↔ `ChatClient` 逐字节 parity 铁律（B 轨测试）的前提。
        """
        ...


class StreamingLLMClient(Protocol):
    """可选扩展：`complete` 的**流式孪生**（Phase 9）。

    刻意**不**并进 `LLMClient`：调用方必须用 `getattr(llm, "stream_complete", None)`
    做能力探测，拿不到就退回 `complete()`。这样只实现三个必需成员的 Fake /
    `agent/eval/real.py::CountingChatClient` 会自动走原路径（调用计数、事件序列等既有
    断言逐字节不变），而实现了它的生产客户端（`ChatClient` / `ModelGateway`）让长报告
    逐字到达而不是静默等一分钟。契约与 `complete` 一致，只是分块返回。
    """

    async def stream_complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> AsyncIterator[str]:
        """流式补全：逐段 yield 文本增量；**拼接结果恒等于 `complete()` 的返回**。"""
        ...
