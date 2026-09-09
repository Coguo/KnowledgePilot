"""MCP 模块（Phase 6）：把研究辅助能力（Memory / Papers）包装成 MCP server。

Agent 作为官方 `mcp` SDK 的 **client**：通过 `MCPGateway`（stdio 子进程 +
ClientSession）连接一组 server，把 server 声明的工具并入 LLM 可调用工具。

**注意：本文件保持纯净（仅 docstring），严禁 re-export `gateway`**——
否则 import 本包就会触发 `import mcp`，破坏 convert / arxiv 等
零 mcp 依赖的离线单测。调用方一律显式子模块导入：
    from knowledge_pilot.mcp.gateway import MCPGateway, build_mcp_specs
    from knowledge_pilot.mcp.convert import to_openai_function_schema
"""
