"""MCP「memory」server：把用户历史研究（SQLite）作为只读工具暴露给 Agent。

启动：`python -m knowledge_pilot.mcp.servers.memory`（网关以子进程拉起）。
需环境变量 MEMORY_DB_PATH（绝对路径）指向研究记忆库。

工具：search_memory（按关键词召回相关历史）/ recent_research（最近记录）。
**绝不 print 到 stdout**（污染 JSON-RPC）；异常经工具内转可读中文返回。
"""

import os
import sys

from mcp.server.fastmcp import FastMCP

from knowledge_pilot.memory.context import build_memory_context
from knowledge_pilot.memory.store import ResearchMemoryStore

# server 实例：工具名 / 描述来自这里，网关 list_tools 拿到的 schema 会转给 LLM。
mcp = FastMCP("knowledge-pilot-memory")

_store: ResearchMemoryStore | None = None


def _get_store() -> ResearchMemoryStore:
    """进程级懒单例（子进程只服务一个父进程会话，无需多实例/线程安全负担）。"""
    global _store
    if _store is None:
        db_path = os.environ.get("MEMORY_DB_PATH", "")
        if not db_path:
            raise RuntimeError("缺少环境变量 MEMORY_DB_PATH（需指向研究记忆库绝对路径）")
        _store = ResearchMemoryStore(db_path)
    return _store


@mcp.tool()
def search_memory(query: str, top_k: int = 3) -> str:
    """在用户的历史研究记录中按关键词召回相关条目（只读，供规划参考，不是网页搜索来源）。"""
    top_k = max(1, min(int(top_k), 20))
    try:
        store = _get_store()
        runs = store.search(query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — 可选增强：转可读中文而非崩溃
        return f"（记忆检索失败：{type(exc).__name__}: {exc}）"
    if not runs:
        return "（未找到相关历史研究记录）"
    return build_memory_context(runs)


@mcp.tool()
def recent_research(limit: int = 10) -> str:
    """列出最近完成的研究记录（只读；含日期/问题/结论摘要/来源）。"""
    limit = max(1, min(int(limit), 50))
    try:
        store = _get_store()
        runs = store.recent(limit)
    except Exception as exc:  # noqa: BLE001
        return f"（记忆读取失败：{type(exc).__name__}: {exc}）"
    if not runs:
        return "（暂无研究历史）"
    return build_memory_context(runs)


if __name__ == "__main__":
    # 仅在作为子进程启动时运行服务（被 import 时不启动，供单测直接检查 mcp 实例）。
    mcp.run()
