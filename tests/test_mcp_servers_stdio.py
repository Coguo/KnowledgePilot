"""真实 stdio 子进程链路（B 轨，需 mcp）：网关如何连接 server 的端到端最小验证。

离线：memory server 读 tmp_path 预置的 SQLite（不联网）；papers 只 list_tools
不真调（search_papers 会调 arXiv，联网留手工冒烟）。

mcp 未装时整模块跳过（importorskip）。
"""

import os
import sys

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from knowledge_pilot.mcp.convert import call_result_to_text, to_openai_function_schema  # noqa: E402
from knowledge_pilot.memory import create_memory_store  # noqa: E402

MEMORY_MODULE = "knowledge_pilot.mcp.servers.memory"
PAPERS_MODULE = "knowledge_pilot.mcp.servers.papers"


def _params(module: str, env: dict | None = None) -> StdioServerParameters:
    """构造 stdio 启动参数（显式全量 env + PYTHONUTF8，与网关同款，兼容新旧 SDK）。"""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        env={**os.environ, "PYTHONUTF8": "1", **(env or {})},
    )


async def test_memory_server_stdio_list_and_call(tmp_path):
    """memory server：真实子进程握手 → 列 2 工具且 schema 可转 OpenAI → search 命中。"""
    db_path = str(tmp_path / "memory.db")
    store = create_memory_store(db_path)
    store.save_run("RAG chunking 策略", report="fixed 与 recursive 对比", sources=[])
    store.close()

    async with stdio_client(_params(MEMORY_MODULE, {"MEMORY_DB_PATH": db_path})) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == {"search_memory", "recent_research"}

            # MCP Tool.inputSchema → OpenAI function schema（LLM 端吃的形状）
            mem_tool = next(t for t in tools if t.name == "search_memory")
            oai = to_openai_function_schema(
                mem_tool.name, mem_tool.description, mem_tool.inputSchema
            )
            assert oai["function"]["name"] == "search_memory"
            assert oai["function"]["parameters"]["type"] == "object"

            # search_memory 命中预置的历史（文本来自 build_memory_context）
            res = await session.call_tool("search_memory", {"query": "RAG chunking", "top_k": 3})
            assert not getattr(res, "isError", False)
            assert "chunking" in call_result_to_text(res)

            # recent_research 非空
            res2 = await session.call_tool("recent_research", {"limit": 5})
            text2 = call_result_to_text(res2)
            assert text2 and "RAG chunking" in text2

            # 无关查询 → 可读空提示
            res3 = await session.call_tool("search_memory", {"query": "量子引力"})
            assert "未找到相关历史研究记录" in call_result_to_text(res3)


async def test_papers_server_stdio_lists_tool(tmp_path):
    """papers server：真实子进程握手 + 列 search_papers（不真调，避免联网）。"""
    async with stdio_client(_params(PAPERS_MODULE)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == {"search_papers"}
