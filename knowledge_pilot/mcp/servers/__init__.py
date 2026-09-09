"""MCP server 模块包：每个文件是一个可独立 `python -m` 启动的 FastMCP stdio server。

由父进程网关以子进程方式启动（`python -m knowledge_pilot.mcp.servers.memory`），
经 stdio JSON-RPC 通信。**server 模块绝不 print 到 stdout**（会污染协议），
诊断/日志一律走 stderr。

注意：本子包 imports mcp（base 依赖），仅被网关按需以子进程加载；
`knowledge_pilot/mcp/__init__.py` 不 import 它们，离线单测不受影响。
"""
