"""LLM 客户端封装。

- 使用 `openai` SDK 对接 OpenAI 兼容接口（DeepSeek / Qwen 等均兼容）。
- 对外只暴露流式增量（内容 / tool_call），由上层负责拼装完整消息。
- 不引入 LangChain / LangGraph：Phase 0 目标就是手写 tool-calling 循环。

`StreamChunk` / `LLMClient`（接口定义）已迁到 `llm/protocol.py`（纯 stdlib，
Model Gateway 与离线测试在无 openai 环境也能 import）；这里 re-export，保持全仓
既有导入路径不变。
"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from knowledge_pilot.config import Settings
from knowledge_pilot.llm.protocol import LLMClient, StreamChunk


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
        extra_body: dict | None = None,
    ) -> str:
        """非流式补全：一次返回完整回复（Query Rewrite / Planner / Evaluate 等短任务用）。

        `extra_body` **仅非 None 时**才写进 body——本方法与 `ModelGateway.complete`
        的外发 JSON 必须逐字节相同（B 轨 parity 铁律），所以这条约定两边一字不差。
        """
        kwargs: dict = {
            "model": self._settings.deepseek_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        resp = await self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def stream_complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> AsyncIterator[str]:
        """`complete()` 的流式孪生：逐段 yield 文本增量（Phase 9 报告逐字输出）。

        传参与 `complete()` 逐个对齐（含 max_tokens/response_format 无条件下发），
        唯一差别是 `stream=True`；不传 tools——`complete()` 也不传，两者 JSON body
        除 `stream` 外完全一致。首个异常直接上抛（不重试：已吐过的字重来会重复）。
        """
        stream = await self._client.chat.completions.create(
            model=self._settings.deepseek_model,
            messages=messages,
            max_tokens=max_tokens,
            response_format=response_format,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue  # 跳过 usage 等无 choices 的收尾块
            content = chunk.choices[0].delta.content
            if content:
                yield content
