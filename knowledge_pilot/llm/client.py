"""LLM 客户端封装。

- 使用 `openai` SDK 对接 OpenAI 兼容接口（DeepSeek / Qwen 等均兼容）。
- 对外只暴露流式增量（内容 / tool_call），由上层负责拼装完整消息。
- 不引入 LangChain / LangGraph：Phase 0 目标就是手写 tool-calling 循环。
"""

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from openai import AsyncOpenAI

from knowledge_pilot.config import Settings


@dataclass
class StreamChunk:
    """一次流式回调携带的增量。content 与 tool_call 至少有一个非空。"""

    content_delta: str | None = None
    # tool_call 增量：{"index", "id"?, "name"?, "arguments"?}，字段可为 None，
    # 由上层按 index 累加拼出完整 tool_call。
    tool_call_delta: dict[str, Any] | None = None


class LLMClient(Protocol):
    """LLM 客户端接口（测试时用 Fake 实现注入）。"""

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
    ) -> str:
        """非流式补全：给定消息返回完整文本（Query Rewrite / Planner / Evaluate 用）。

        response_format 透传给 OpenAI 兼容接口（如 {"type": "json_object"}）。
        """
        ...


class ChatClient:
    """OpenAI 兼容 LLM 客户端（默认 DeepSeek）。"""

    def __init__(self, settings: Settings) -> None:
        if not settings.has_api_key:
            raise ValueError(
                "未配置 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入密钥。"
            )
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )

    @property
    def model(self) -> str:
        return self._settings.deepseek_model

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        stream = await self._client.chat.completions.create(
            model=self._settings.deepseek_model,
            messages=messages,
            tools=tools,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue  # 跳过 usage 等无 choices 的收尾块
            delta = chunk.choices[0].delta
            if delta.content:
                yield StreamChunk(content_delta=delta.content)
            for tc in delta.tool_calls or []:
                yield StreamChunk(
                    tool_call_delta={
                        "index": tc.index,
                        "id": tc.id,
                        "name": tc.function.name if tc.function else None,
                        "arguments": tc.function.arguments if tc.function else None,
                    }
                )

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        """非流式补全：一次返回完整回复（Query Rewrite / Planner / Evaluate 等短任务用）。"""
        resp = await self._client.chat.completions.create(
            model=self._settings.deepseek_model,
            messages=messages,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        return resp.choices[0].message.content or ""
