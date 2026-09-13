"""MCP「memory」server：把用户历史研究（SQLite）作为只读工具暴露给 Agent。

启动：`python -m knowledge_pilot.mcp.servers.memory`（网关以子进程拉起）。
需环境变量 MEMORY_DB_PATH（绝对路径）指向研究记忆库。

工具：search_memory（按关键词召回相关历史）/ recent_research（最近记录）。
**绝不 print 到 stdout**（污染 JSON-RPC）；异常经工具内转可读中文返回。

Phase 8：server 进程长驻复用后，不再缓存进程级 `_store` 单例——每次 tool call
**现开现关** store（`open_read_store`）。原因见 store.py 该函数 docstring：长驻连接
会缓存旧快照（父进程两请求间写库后读不到新数据）、并在 Windows 上常驻 db 文件句柄
（父进程/测试重建或删除文件会 PermissionError）。开销微秒级，可忽略。
"""

import os

from mcp.server.fastmcp import FastMCP

from knowledge_pilot.memory.context import build_memory_context
from knowledge_pilot.memory.store import open_read_store

# server 实例：工具名 / 描述来自这里，网关 list_tools 拿到的 schema 会转给 LLM。
mcp = FastMCP("knowledge-pilot-memory")


def _open_fresh():
    """按 MEMORY_DB_PATH 现开一个只读 store；打开失败返回 None（可读兜底在调用方）。"""
    try:
        return open_read_store(os.environ.get("MEMORY_DB_PATH", ""))
    except Exception:  # noqa: BLE001 — 环境缺失/路径无效属配置问题，转可读文本
        return None


@mcp.tool()
def search_memory(query: str, top_k: int = 3) -> str:
    """在用户的历史研究记录中按关键词召回相关条目（只读，供规划参考，不是网页搜索来源）。"""
    top_k = max(1, min(int(top_k), 20))
    store = _open_fresh()
    if store is None:
        return "（记忆存储不可用：缺少或无法打开 MEMORY_DB_PATH 指向的数据库）"
    try:
        runs = store.search(query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — 可选增强：转可读中文而非崩溃
        return f"（记忆检索失败：{type(exc).__name__}: {exc}）"
    finally:
        store.close()
    if not runs:
        return "（未找到相关历史研究记录）"
    return build_memory_context(runs)


@mcp.tool()
def recent_research(limit: int = 10) -> str:
    """列出最近完成的研究记录（只读；含日期/问题/结论摘要/来源）。"""
    limit = max(1, min(int(limit), 50))
    store = _open_fresh()
    if store is None:
        return "（记忆存储不可用：缺少或无法打开 MEMORY_DB_PATH 指向的数据库）"
    try:
        runs = store.recent(limit)
    except Exception as exc:  # noqa: BLE001
        return f"（记忆读取失败：{type(exc).__name__}: {exc}）"
    finally:
        store.close()
    if not runs:
        return "（暂无研究历史）"
    return build_memory_context(runs)


if __name__ == "__main__":
    # 仅在作为子进程启动时运行服务（被 import 时不启动，供单测直接检查 mcp 实例）。
    mcp.run()
