"""Model Gateway（Phase 8，务实内核）：统一模型访问层，实现 `LLMClient` Protocol。

规格 §12 把它定义为「Agent 与具体模型之间的统一访问层」，职责含 Routing / Timeout /
Retry / Fallback / Streaming / Token Usage / Logging。按仓库「只加解决实际问题的
技术」约束，这里做成务实内核：

- **Provider 注册表**：有序 provider 列表（primary 恒为 DeepSeek，来自既有
  `deepseek_*` 配置）；仅在 `LLM_FALLBACK_ENABLED` 时把 `LLM_EXTRA_PROVIDERS`
  （OpenAI 兼容，如 Qwen）追加为 fallback 目标。默认单 provider → 路由透明。
- **Timeout**：`LLM_TIMEOUT` 设置时传给 AsyncOpenAI（不设则不传 → SDK 默认，
  与旧 `ChatClient` 一致）。
- **Retry**：仅对瞬时错误（连接/超时/429/5xx，判定在 `llm/errors.py`）退避重试；
  400/401/403/404 等语义错误原样上抛（不改变 planner/evaluate/kg/judge 既有解析失败
  回退语义）。开启自管重试/fallback 时给 provider 传 `max_retries=0` 防与 SDK 默认
  重试叠乘。
- **Fallback**：单 provider 重试耗尽且瞬时错 → 顺延下一 provider。
- **Streaming**：只对「首字节前」的打开阶段重试；首字节后出错绝不重试（防重复产出）。
- **Token Usage / Logging**：仅 `LLM_LOG_USAGE` 开启时发 `include_usage` 并在流末尾
  采集 usage，经 stdlib logging 打 stderr（不污染 stdout）或注入的 usage_logger。

默认配置（无 timeout / 无重试 / 无 fallback / 无 extra / 无 usage log）下，每个
调用只发一次、不含 `stream_options`，超时/重试用 SDK 默认 → 与 `ChatClient`
**逐字节一致**（B 轨 parity 测试锁定）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from knowledge_pilot.config import Settings
from knowledge_pilot.llm.errors import backoff_seconds, is_transient_error
from knowledge_pilot.llm.protocol import StreamChunk
from knowledge_pilot.llm.providers import ProviderClient

logger = logging.getLogger("knowledge_pilot.llm.gateway")


def _usage_to_dict(usage: Any) -> dict | None:
    """把 openai 的 usage 对象（或任意带 token 字段的对象）压成日志 dict。"""
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


class ModelGateway:
    """统一模型访问层。实现 `LLMClient` Protocol → graph/loop/rag/rewrite/judge
    等消费方零改动（它们只依赖 Protocol 的 `model` / `complete` / `stream_chat`）。"""

    def __init__(
        self,
        providers: list[ProviderClient],
        *,
        retry_enabled: bool = False,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        log_usage: bool = False,
        usage_logger: Callable[[dict], None] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("ModelGateway: 至少需要一个 provider")
        self._providers = list(providers)
        self._retry_enabled = retry_enabled
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff_seconds
        self._log_usage = log_usage
        self._usage_logger = usage_logger

    # ---- LLMClient Protocol -------------------------------------------

    @property
    def model(self) -> str:
        """对外暴露的 model = primary（DeepSeek）的 model。"""
        return self._providers[0].model

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        """非流式补全：瞬时错退避重试 + 单 provider 耗尽可 fallback。

        非瞬时错误（400/401/403/404 等）立即上抛，语义与旧 `ChatClient` 一致——
        调用方的解析失败回退（planner 单步 / evaluate 充分 / KG 空 / judge rubric）在
        「成功返回但内容不合规」的分支触发，不受这里影响。
        """
        last_exc: BaseException | None = None
        for i, provider in enumerate(self._providers):
            attempts = self._max_attempts if self._retry_enabled else 1
            for attempt in range(1, attempts + 1):
                try:
                    text, usage = await provider.complete(
                        messages, max_tokens=max_tokens, response_format=response_format
                    )
                    self._record_usage("complete", provider, usage)
                    return text
                except Exception as exc:  # noqa: BLE001 — 分类决定是否重试
                    last_exc = exc
                    if not is_transient_error(exc):
                        raise
                    if attempt < attempts:
                        await asyncio.sleep(backoff_seconds(attempt, self._backoff))
                        continue
                    # 本 provider 重试耗尽：瞬时错且有 fallback 则换下一家，否则上抛。
                    if i + 1 < len(self._providers):
                        logger.info(
                            "llm complete provider %s 重试耗尽，fallback→%s",
                            provider.name,
                            self._providers[i + 1].name,
                        )
                        break
                    raise
        raise last_exc if last_exc is not None else RuntimeError("ModelGateway.complete: 无可用 provider")

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式：只在「首字节前」的打开阶段重试/fallback；打开成功后消费。

        - 内容增量 / tool_call 增量按 openai 流式块透传（与 `ChatClient.stream_chat`
          逐字节一致）；`usage-only` 收尾块不 yield，仅在 llm_log_usage 时记 usage。
        - 首字节后出错原样上抛（不重试，避免重复产出片段）。
        """
        stream, provider = await self._open_stream_with_retries(messages, tools)
        usage: Any = None
        try:
            async for chunk in stream:
                if not chunk.choices:
                    # usage-only 收尾块（无 choices）：记录但不 yield（与 ChatClient 一致）。
                    if self._log_usage:
                        u = getattr(chunk, "usage", None)
                        if u is not None:
                            usage = u
                    continue
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
        finally:
            if self._log_usage and usage is not None:
                self._record_usage("stream", provider, usage)

    async def stream_complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """`complete()` 的流式孪生：逐段 yield 文本增量（Phase 9 报告逐字输出）。

        复用 `_open_stream_with_retries` → 重试/fallback 同样**只发生在首字节之前**：
        报告流一旦吐过字就绝不重来，否则用户会看到重复片段。tool_call 增量与 usage-only
        收尾块一律忽略（本方法与 `complete()` 语义对齐，不返回工具调用；usage 仅在
        llm_log_usage 开启时记账）。
        """
        stream, provider = await self._open_stream_with_retries(
            messages, None, max_tokens=max_tokens
        )
        usage: Any = None
        try:
            async for chunk in stream:
                if not chunk.choices:
                    if self._log_usage:
                        u = getattr(chunk, "usage", None)
                        if u is not None:
                            usage = u
                    continue
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        finally:
            if self._log_usage and usage is not None:
                self._record_usage("stream", provider, usage)

    # ---- 内部 -----------------------------------------------------------

    async def _open_stream_with_retries(
        self, messages: list[dict], tools: list[dict] | None, *, max_tokens: int | None = None
    ) -> tuple[Any, ProviderClient]:
        """打开流（await create）。瞬时错可重试/可 fallback；非瞬时错立即上抛。

        `max_tokens` **只在非 None 时**才透传给 provider——默认路径（stream_chat）的
        调用参数与 Phase 8 逐字节一致，喂给网关的鸭子类型假 provider 也不会多收参数。
        """
        last_exc: BaseException | None = None
        for i, provider in enumerate(self._providers):
            attempts = self._max_attempts if self._retry_enabled else 1
            for attempt in range(1, attempts + 1):
                try:
                    open_kwargs: dict = {"include_usage": self._log_usage}
                    if max_tokens is not None:
                        open_kwargs["max_tokens"] = max_tokens
                    stream = await provider.open_stream(messages, tools, **open_kwargs)
                    return stream, provider
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if not is_transient_error(exc):
                        raise
                    if attempt < attempts:
                        await asyncio.sleep(backoff_seconds(attempt, self._backoff))
                        continue
                    if i + 1 < len(self._providers):
                        logger.info(
                            "llm stream provider %s 重试耗尽，fallback→%s",
                            provider.name,
                            self._providers[i + 1].name,
                        )
                        break
                    raise
        raise last_exc if last_exc is not None else RuntimeError("ModelGateway.stream_chat: 无可用 provider")

    def _record_usage(self, kind: str, provider: ProviderClient, usage: Any) -> None:
        """usage 记录：仅 llm_log_usage 开启且服务端返回了 usage 时触发。"""
        if not self._log_usage:
            return
        usage_dict = _usage_to_dict(usage)
        if usage_dict is None:
            return
        entry = {"kind": kind, "provider": provider.name, "model": provider.model, **usage_dict}
        if self._usage_logger is not None:
            self._usage_logger(entry)
        logger.info(
            "llm %s provider=%s model=%s usage=%s",
            kind,
            provider.name,
            provider.model,
            usage_dict,
        )


def build_llm_client(settings: Settings) -> ModelGateway:
    """按 settings 装配 ModelGateway（生产构造点用）。

    默认（全关/空）→ 单 DeepSeek provider，逐字节等价旧 `ChatClient(settings)`。
    """
    if not settings.has_api_key:
        raise ValueError(
            "未配置 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入密钥。"
        )
    # 网关自管重试/fallback 时关掉 SDK 内置重试（max_retries=0），防叠乘；
    # 默认（都不开）不传该参数 → 保留 SDK 默认，与 ChatClient 一致。
    manage_retries = settings.llm_retry_enabled or settings.llm_fallback_enabled
    max_retries = 0 if manage_retries else None
    timeout = settings.llm_timeout

    providers: list[ProviderClient] = [
        ProviderClient(
            name="deepseek",
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
    ]
    if settings.llm_fallback_enabled:
        for extra in settings.llm_extra_providers:
            if not extra.api_key.strip():
                continue
            providers.append(
                ProviderClient(
                    name=extra.name,
                    model=extra.model,
                    base_url=extra.base_url,
                    api_key=extra.api_key,
                    timeout=extra.timeout,
                    max_retries=max_retries,
                )
            )

    return ModelGateway(
        providers,
        retry_enabled=settings.llm_retry_enabled,
        max_attempts=settings.llm_retry_max_attempts,
        backoff_seconds=settings.llm_retry_backoff_seconds,
        log_usage=settings.llm_log_usage,
    )
