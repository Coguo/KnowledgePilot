"""手写 tool-calling 循环：用户问题 → LLM(流式) → 必要时执行工具 → 最终答案。

引擎只依赖 llm / search 的接口（LLMClient / SearchProvider），
与 Web / 桌面 UI 完全无关，可独立测试（测试注入 Fake LLM，不联网）。
"""

import json
from collections.abc import AsyncIterator

from knowledge_pilot.agent.events import (
    DoneEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from knowledge_pilot.agent.tools import ALL_TOOLS, run_tool
from knowledge_pilot.llm.client import LLMClient
from knowledge_pilot.search.base import SearchProvider

# 工具调用轮次上限：防止模型陷入无限调用工具的循环（Phase 0 简单兜底）。
MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = (
    "你是一个 AI 研究助手，任务是为用户的研究问题给出清晰、有依据的回答。\n"
    "规则：\n"
    "1. 当问题需要外部或最新信息（具体资料、论文、技术文档、对比数据等）时，"
    "先调用 search_web 获取资料，再基于资料作答。\n"
    "2. 回答用中文，结构清晰，尽量引用使用的资料来源（标题 + URL）。\n"
    "3. 如果问题不需要搜索，直接回答。\n"
)


async def run_research(
    query: str,
    *,
    llm: LLMClient,
    search: SearchProvider,
    rag: object | None = None,  # RAGPipeline，透传给工具；None 时行为与 Phase 0 一致
) -> AsyncIterator[object]:
    """运行一次研究会话，产出事件流（TokenEvent / ToolCallEvent / ToolResultEvent / DoneEvent）。"""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    final_answer = ""
    rounds = 0

    while True:
        rounds += 1
        # 1) 流式调用 LLM：边收文本增量，边按 index 累加 tool_call 增量。
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}

        async for chunk in llm.stream_chat(messages, tools=ALL_TOOLS):
            if chunk.content_delta is not None:
                content_parts.append(chunk.content_delta)
                yield TokenEvent(chunk.content_delta)
            if chunk.tool_call_delta is not None:
                _accumulate_tool_call(tool_calls, chunk.tool_call_delta)

        assistant_message: dict = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        if tool_calls:
            assistant_message["tool_calls"] = [
                tool_calls[i] for i in sorted(tool_calls)
            ]
        messages.append(assistant_message)

        # 2) 没有工具调用 → 输出即为最终答案。
        if not tool_calls:
            final_answer = "".join(content_parts)
            break

        # 2.5) 达到轮次上限仍请求工具 → 兜底结束，避免无限循环。
        if rounds >= MAX_TOOL_ROUNDS:
            break

        # 3) 执行工具，把结果作为 tool message 回填，进入下一轮。
        for index in sorted(tool_calls):
            tc = tool_calls[index]
            name = tc["function"]["name"]
            arguments_text = tc["function"]["arguments"]
            arguments = json.loads(arguments_text or "{}")

            yield ToolCallEvent(name=name, arguments=arguments_text)
            result = await run_tool(name, arguments, search=search, rag=rag)
            yield ToolResultEvent(name=name, summary=_summarize(result))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    yield DoneEvent(content=final_answer)


def _accumulate_tool_call(acc: dict[int, dict], delta: dict) -> None:
    """把一次流式 tool_call 增量并入按 index 分组的累加器。"""
    index = delta.get("index", 0)
    entry = acc.setdefault(
        index,
        {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
    )
    if delta.get("id"):
        entry["id"] = delta["id"]
    if delta.get("name"):
        entry["function"]["name"] += delta["name"]
    if delta.get("arguments"):
        entry["function"]["arguments"] += delta["arguments"]


def _summarize(result: str) -> str:
    """从工具返回文本里抽一句话给 UI 展示。"""
    first_line = result.splitlines()[0] if result else ""
    if len(first_line) > 80:
        return first_line[:80] + "…"
    return first_line
