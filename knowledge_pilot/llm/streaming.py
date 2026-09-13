"""`complete()` / `stream_complete()` 的**能力探测式降级**（Phase 9）。

两个调用点需要同一件事——「拿到一段长文本，能流就边流边回调，不能流就一次性取回」：

- `agent/graph.py::_report_text`（研究报告逐字输出）；
- `learning/session.py::run_node_chat`（知识点讲解逐字输出）。

所以这份判断只写一次。**不要**把 `stream_complete` 加进 `LLMClient` Protocol 再强制所有
实现都提供：那会让测试里的 Fake 客户端、`agent/eval/real.py::CountingChatClient` 等一批
只实现 `complete` 的对象全部要改，而它们的「非流式」正是既有约 40 条断言的基准。
能力探测让「有流用流、没流退回」成为默认行为，新老调用方都不必改签名。
"""

from typing import Callable

from knowledge_pilot.llm.protocol import LLMClient


def stream_capable(llm: LLMClient) -> bool:
    """这个客户端支持逐段流式补全吗？

    单独暴露是因为有个调用方**必须**自己做循环：`learning/session.py::run_node_chat`
    是异步生成器，每段增量要当场 `yield` 给用户——而异步生成器只能在**自己的函数体**
    里 yield，没法通过回调转交。所以那里用本函数做判断、自己写循环；
    不需要 yield 的调用方（如报告）直接用下面的 `stream_text`。
    """
    return getattr(llm, "stream_complete", None) is not None


async def stream_text(
    llm: LLMClient,
    messages: list[dict],
    *,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    on_delta: Callable[[str], None] | None = None,
) -> str:
    """取全文：有 `stream_complete` 就逐段回调，否则退回 `complete`（一次性）。

    **返回值恒等于流出去的各段拼接**（不变量）。降级路径**不**把整段文本补成一次
    `on_delta` 回调——那会改变既有调用方观察到的事件序列（graph 模式靠 `DoneEvent`
    兜底渲染正文，补发一个 TokenEvent 就会多出一帧，既有测试与前端行为都会变）。
    """
    stream_complete = getattr(llm, "stream_complete", None)
    if stream_complete is None:
        return await llm.complete(messages, max_tokens=max_tokens)

    kwargs: dict = {"max_tokens": max_tokens}
    if response_format is not None:
        kwargs["response_format"] = response_format

    parts: list[str] = []
    async for delta in stream_complete(messages, **kwargs):
        if not delta:
            continue  # 有些 provider 会发空增量（心跳/收尾块）
        parts.append(delta)
        if on_delta is not None:
            on_delta(delta)
    return "".join(parts)
