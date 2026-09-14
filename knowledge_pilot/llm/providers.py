"""Model providers：单个 provider 的调用边界 + 进程级共享 AsyncOpenAI 客户端。

顶层不 import openai（沙箱/离线 A 轨 import-safe）：`openai` 只在
`get_shared_async_openai()` / `_ensure_client()` 内懒导入。`ProviderClient` 与网关
（`llm/gateway.py`）因此可在无 openai 的环境构造与单测——A 轨的重试/fallback/流式
决策用**假 ProviderClient**（鸭子：`name/model` + `complete`/`open_stream`）驱动，
真实 ProviderClient 只在 B 轨（装了 openai + httpx.MockTransport）里验。
"""

from __future__ import annotations

from typing import Any

# provider 方言：关掉推理模型的 thinking（第七轮）。
#
# 为什么需要它：推理模型（`DEEPSEEK_MODEL=deepseek-flash` 这类）的 `reasoning_content`
# 与正文**共用同一个 `max_tokens`**。实测一次真实的知识点抽取：8192 的额度里推理吃掉
# 19474 字、正文只剩 1418 字且被截断在半句（`finish_reason=length`）——一个节点都没解析
# 出来。同一份输入、同一个 prompt，加上这个开关后 reasoning 为 0、正文 3386 字、
# `finish_reason=stop`、12 个节点全部解析成功。
#
# 放在这里而不是 `protocol.py`：它是**某一个 provider 的方言**，只有说 SDK 那门语言的
# 这一层该认识它。上层（`agent/graph.py` / `learning/outline.py`）只把它当不透明字典传下来。
THINKING_OFF: dict[str, Any] = {"thinking": {"type": "disabled"}}


# 进程级共享 AsyncOpenAI：同一 (base_url, api_key, timeout, max_retries) 只建一次
# （AsyncOpenAI 线程安全、内部复用连接）。注入 http_client 的实例不入共享缓存。
_client_cache: dict[tuple, Any] = {}


def get_shared_async_openai(
    base_url: str,
    api_key: str,
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
):
    """按 (base_url, api_key, timeout, max_retries) 取进程级共享 AsyncOpenAI 客户端。"""
    from openai import AsyncOpenAI

    key = (base_url, api_key, timeout, max_retries)
    client = _client_cache.get(key)
    if client is None:
        kwargs: dict = {"api_key": api_key, "base_url": base_url}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        client = AsyncOpenAI(**kwargs)
        _client_cache[key] = client
    return client


class ProviderClient:
    """一个 provider（name + model + base_url + api_key）的单次调用边界。

    重试/退避决策归网关（`ModelGateway`）所有；本类只做单次请求：
    - 网关开启自管重试/fallback 时必须传 `max_retries=0`（否则与 openai SDK 内置的
      默认 2 次传输重试叠乘）；默认不传该参数 → 保留 SDK 默认，与旧 `ChatClient`
      逐字节一致。
    - `complete()` 返回 `(text, usage_or_None)`；流式 `open_stream()` 只负责建流。
    """

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: Any | None = None,  # B 轨测试注入 httpx.MockTransport 用
    ) -> None:
        if not api_key:
            raise ValueError(f"provider {name}: 缺少 api_key（请配置对应环境变量）")
        self.name = name
        self.model = model
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client = http_client
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._http_client is not None:
            from openai import AsyncOpenAI

            kwargs: dict = {
                "api_key": self._api_key,
                "base_url": self._base_url,
                "http_client": self._http_client,
            }
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            if self._max_retries is not None:
                kwargs["max_retries"] = self._max_retries
            self._client = AsyncOpenAI(**kwargs)
        else:
            self._client = get_shared_async_openai(
                self._base_url,
                self._api_key,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        return self._client

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        extra_body: dict | None = None,
    ) -> tuple[str, Any]:
        """非流式补全：返回 (完整文本, usage 对象或 None)。

        `extra_body` **仅非 None 时**才写进 body（`max_tokens` 同一条约定）：默认路径
        的外发 JSON 因此逐字节不变，B 轨 parity 铁律照旧成立。
        """
        client = self._ensure_client()
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        resp = await client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return content, usage

    async def open_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        include_usage: bool = False,
        max_tokens: int | None = None,
    ) -> Any:
        """创建流式响应（只 await create，不消费）。调用方 async for 消费。

        `tools` **无条件传给 SDK**（即使 None 也发 `null`）——与 `ChatClient.stream_chat`
        的传参方式逐字节一致（传与不传在 JSON body 层级不同，B 轨 parity 测试锁定）。
        `include_usage=True` 时额外发 `stream_options={"include_usage": True}`（仅网关在
        llm_log_usage 开启时使用，否则保持与 ChatClient 逐字节一致）。

        `max_tokens` **仅非 None 时**才写进 body（Phase 9 报告流式用），默认不传 →
        外发 JSON 与 `ChatClient.stream_chat` 逐字节一致，既有 parity 测试不受影响。
        """
        client = self._ensure_client()
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": True,
        }
        if include_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return await client.chat.completions.create(**kwargs)
