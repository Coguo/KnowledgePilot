"""MCP「papers」server：arXiv 论文检索（只读、无 key）暴露给 Agent。

启动：`python -m knowledge_pilot.mcp.servers.papers`（网关以子进程拉起）。
**绝不 print 到 stdout**（污染 JSON-RPC）；网络/解析异常在工具内转可读中文。
"""

import sys

from mcp.server.fastmcp import FastMCP

from knowledge_pilot.mcp.arxiv import format_papers, search_arxiv

# server 实例：工具名 / 描述来自这里，网关 list_tools 拿到的 schema 会转给 LLM。
mcp = FastMCP("knowledge-pilot-papers")


@mcp.tool()
async def search_papers(query: str, max_results: int = 5) -> str:
    """在 arXiv 预印本库检索论文（返回编号标题/作者/URL/摘要；只读辅助，不是网页搜索）。"""
    max_results = max(1, min(int(max_results), 30))
    try:
        items = await search_arxiv(query, max_results=max_results)
    except Exception as exc:  # noqa: BLE001 — arXiv 抖动/网络异常 → 可读中文提示
        return f"（arXiv 检索失败：{type(exc).__name__}: {exc}）"
    return format_papers(items)


if __name__ == "__main__":
    # 仅在作为子进程启动时运行服务（被 import 时不启动，供单测直接检查 mcp 实例）。
    mcp.run()
