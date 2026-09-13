"""手写 tool-calling 循环：用户问题 → LLM(流式) → 必要时执行工具 → 最终答案。

引擎只依赖 llm / search 的接口（LLMClient / SearchProvider），
与 Web / 桌面 UI 完全无关，可独立测试（测试注入 Fake LLM，不联网）。
"""

import json
from collections.abc import AsyncIterator, Callable

from knowledge_pilot.agent.events import (
    DoneEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from knowledge_pilot.agent.tools import ALL_TOOLS, run_tool
from knowledge_pilot.llm.protocol import LLMClient
from knowledge_pilot.search.base import SearchProvider, SearchResult

# 工具调用轮次上限：防止模型陷入无限调用工具的循环（Phase 0 简单兜底）。
# Phase 9：本常量仍是**默认值**（调用方不传 max_tool_rounds 时生效），可被运行期覆盖
# 成 config.agent_max_tool_rounds 或请求级值；保留常量是因为有测试直接 import 它。
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
    on_search_results: Callable[[list[SearchResult]], None] | None = None,
    system_prompt: str | None = None,
    tools: list[dict] | None = None,  # 工具 schema 列表；None → 默认 ALL_TOOLS
    mcp: object | None = None,  # 打开的 MCPGateway（Phase 6）；None 时行为与 Phase 5 一致
    on_extra_tool_result: Callable[[str, str], None] | None = None,  # (工具名, 文本结果) 钩子
    max_tool_rounds: int | None = None,  # Phase 9：覆盖 MAX_TOOL_ROUNDS；None → 用常量
) -> AsyncIterator[object]:
    """运行一次研究会话，产出事件流（TokenEvent / ToolCallEvent / ToolResultEvent / DoneEvent）。

    on_search_results：每次 search_web 拿到结构化搜索结果后回调（Phase 3 图节点采证用，
    默认 None 行为不变）。system_prompt：覆盖默认系统提示词（研究节点用研究导向提示）。

    mcp / tools / on_extra_tool_result（Phase 6，默认 None 时与 Phase 5 逐字节一致）：
    - tools：非 None 时覆盖默认工具列表（ALL_TOOLS）；mcp 连接了工具时在其后追加 MCP schema。
    - mcp：已打开的 MCP 网关，其声明的工具并入 LLM 可调用列表，执行走 _dispatch_tool。
    - on_extra_tool_result：每次 MCP 工具执行后回调（name, 文本结果）——图节点用它把
      MCP 输出落 notes（内层 LLM token 会被丢弃，不落库则 MCP 结果到不了最终报告）。
    """
    messages: list[dict] = [
        {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    # 有效工具列表：mcp=None 且 tools=None 时仍是 ALL_TOOLS 本身（与 Phase 5 相等）。
    base_tools = ALL_TOOLS if tools is None else tools
    effective_tools = base_tools
    mcp_names = list(mcp.names()) if mcp is not None else []
    if mcp_names:
        effective_tools = list(base_tools) + list(mcp.tool_schemas())

    final_answer = ""
    rounds = 0
    # None → 常量（与 Phase 0-8 逐字节一致）；调用方覆盖时以覆盖值为准。
    rounds_limit = MAX_TOOL_ROUNDS if max_tool_rounds is None else max_tool_rounds

    while True:
        rounds += 1
        # 1) 流式调用 LLM：边收文本增量，边按 index 累加 tool_call 增量。
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}

        async for chunk in llm.stream_chat(messages, tools=effective_tools):
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
        if rounds >= rounds_limit:
            break

        # 3) 执行工具，把结果作为 tool message 回填，进入下一轮。
        for index in sorted(tool_calls):
            tc = tool_calls[index]
            name = tc["function"]["name"]
            arguments_text = tc["function"]["arguments"]
            arguments = json.loads(arguments_text or "{}")

            yield ToolCallEvent(name=name, arguments=arguments_text)
            result = await _dispatch_tool(
                name,
                arguments,
                search=search,
                rag=rag,
                on_search_results=on_search_results,
                mcp=mcp,
                on_extra_tool_result=on_extra_tool_result,
            )
            yield ToolResultEvent(name=name, summary=_summarize(result))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    yield DoneEvent(content=final_answer)


async def _dispatch_tool(
    name: str,
    arguments: dict,
    *,
    search: SearchProvider,
    rag: object | None,
    on_search_results: Callable[[list[SearchResult]], None] | None,
    mcp: object | None,
    on_extra_tool_result: Callable[[str, str], None] | None,
) -> str:
    """按工具名路由执行：search_web 走原生 run_tool；MCP 工具走网关。

    search_web 刻意保持原生进程内执行（证据采集 on_search_results + RAG 增强
    依赖父进程里的结构化 SearchResult，stdio MCP 只回文本会断这两者）。
    MCP 工具返回自由文本，经 on_extra_tool_result 在工具边界采集（图节点落 notes），
    否则文本只在本轮 tool message 给 LLM、进不了最终报告。
    """
    if name == "search_web":
        # run_tool 的签名与 ValueError 契约原样保留（直调它的测试不受影响）。
        return await run_tool(
            name,
            arguments,
            search=search,
            rag=rag,
            on_search_results=on_search_results,
        )
    if mcp is not None and mcp.has(name):
        try:
            text = await mcp.call(name, arguments)
        except Exception as exc:  # noqa: BLE001 — MCP 是可选的补充资料：失败不崩整个研究，
            # 转为可读文本回填给模型（模型可据此调整），而不是让工具轮异常中断。
            return f"（MCP 工具 {name} 暂不可用：{type(exc).__name__}: {exc}）"
        if on_extra_tool_result is not None:
            on_extra_tool_result(name, text)
        return text
    raise ValueError(f"未知工具: {name!r}")


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
