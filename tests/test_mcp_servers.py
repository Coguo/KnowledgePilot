"""MCP server 模块级冒烟（B 轨，需 mcp）：可 import、是 FastMCP 实例、
注册了预期工具与参数 schema。

mcp 未装时整模块跳过（importorskip）；不联网、不 spawn 子进程
（真实 stdio 链路在 test_mcp_servers_stdio.py）。
"""

import inspect

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp import FastMCP  # noqa: E402

from knowledge_pilot.mcp.servers import memory as memory_server  # noqa: E402
from knowledge_pilot.mcp.servers import papers as papers_server  # noqa: E402


async def _list_tools(server: FastMCP) -> list[dict]:
    """枚举 server 注册的工具 → [{"name", "description", "inputSchema"}]。

    优先用**公开** API `FastMCP.list_tools()`：它返回 MCP 协议层的
    `mcp.types.Tool`（字段 inputSchema），正是网关/客户端实际看到的形状。
    仅在公开 API 缺席时退回内部的 `_tool_manager.list_tools()`——mcp ≥1.30 那里
    是同步方法且返回 FastMCP 自己的 Tool（schema 字段名从 v1 的 inputSchema 变成了
    parameters），故两种命名都认。不 import mcp 的具体内部类型，只按属性取值。
    """
    public = getattr(server, "list_tools", None)
    if public is not None:
        result = public()
        if inspect.isawaitable(result):
            result = await result
    else:
        mgr = getattr(server, "_tool_manager", None)
        list_tools = getattr(mgr, "list_tools", None) if mgr is not None else None
        assert list_tools is not None, "FastMCP 实例无法枚举工具（SDK 结构变化？）"
        result = list_tools()
        if inspect.isawaitable(result):
            result = await result
    return [
        {
            "name": t.name,
            "description": getattr(t, "description", "") or "",
            "inputSchema": getattr(t, "inputSchema", None) or getattr(t, "parameters", {}) or {},
        }
        for t in result
    ]


def test_servers_are_fastmcp_instances():
    assert isinstance(memory_server.mcp, FastMCP)
    assert isinstance(papers_server.mcp, FastMCP)


async def test_memory_server_registers_tools():
    tools = await _list_tools(memory_server.mcp)
    by_name = {t["name"]: t for t in tools}
    assert {"search_memory", "recent_research"} <= set(by_name)

    search_memory = by_name["search_memory"]
    assert search_memory["inputSchema"].get("type") == "object"
    props = search_memory["inputSchema"].get("properties", {})
    assert "query" in props and "top_k" in props

    recent = by_name["recent_research"]
    assert "limit" in recent["inputSchema"].get("properties", {})


async def test_papers_server_registers_tool():
    tools = await _list_tools(papers_server.mcp)
    assert {t["name"] for t in tools} == {"search_papers"}

    search_papers = next(t for t in tools if t["name"] == "search_papers")
    assert search_papers["inputSchema"].get("type") == "object"
    props = search_papers["inputSchema"].get("properties", {})
    assert "query" in props and "max_results" in props
