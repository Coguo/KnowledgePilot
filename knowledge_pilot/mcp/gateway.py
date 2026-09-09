"""MCP 网关：Agent 作为官方 `mcp` SDK 的 **client**，连接一组 stdio server。

本模块 imports mcp（base 依赖）。因此**调用方必须显式子模块导入本文件**
（`from knowledge_pilot.mcp.gateway import MCPGateway`）；`mcp/__init__.py`
保持纯净不 re-export，否则离线单测（convert/arxiv/config）会被 import mcp 拖垮。

生命周期：stdio client 是 async 的 → 网关在 async 上下文里以
`async with MCPGateway(specs):` 打开/关闭（每请求一次，__aexit__ 关掉
AsyncExitStack 并杀光子进程）。对调用方暴露的统一接口只有 5 个方法：
    names() / has(name) / tool_schemas() / prompt_hint() / async call(name, args)
——mcp v1→v2 升级的爆炸半径被圈在 call_result_to_text 与内部协议调用里。
"""

import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from knowledge_pilot.mcp.convert import call_result_to_text, to_openai_function_schema


@dataclass
class MCPServerSpec:
    """一个 MCP stdio server 的启动描述（固定注册表，新增 server = 加一行）。"""

    name: str  # 诊断用（memory / papers），不参与工具路由
    module: str  # "knowledge_pilot.mcp.servers.memory"，以 `python -m` 启动
    env: dict | None = None  # 注入子进程的额外环境变量（如 MEMORY_DB_PATH）


class MCPGateway:
    """聚合一组 stdio server 的工具，对外呈现统一的路由与 schema 列表。"""

    def __init__(self, specs: list[MCPServerSpec]) -> None:
        self._specs = specs
        self._stack: AsyncExitStack | None = None
        self._route: dict[str, ClientSession] = {}  # 工具名 → 会话
        self._schemas: list[dict] = []  # OpenAI function schema（并入 ALL_TOOLS）
        self._tool_meta: list[tuple[str, str]] = []  # (name, description) 供 prompt_hint

    # ---- 生命周期 -------------------------------------------------------

    async def __aenter__(self) -> "MCPGateway":
        """逐个启动 server 并握手；单个失败只告警 stderr 跳过（MCP 是可选增强，
        绝不因某个 server 挂掉整个研究）。"""
        self._stack = AsyncExitStack()
        for spec in self._specs:
            try:
                await self._connect(spec)
            except Exception as exc:  # noqa: BLE001 — 可选增强：失败跳过而非上抛
                print(f"[mcp] server {spec.module} 启动失败，已跳过：{exc}", file=sys.stderr)
        return self

    async def __aexit__(self, *exc) -> None:
        """关栈：结束所有 ClientSession / stdio 子进程。"""
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None

    async def _connect(self, spec: MCPServerSpec) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", spec.module],
            # 显式全量传递并补 PYTHONUTF8：老 SDK env 整体替换、新 SDK 合并，
            # 显式完整 env 两者都安全；PYTHONUTF8=1 保证中文子进程 stdout 编码正确。
            env={**os.environ, "PYTHONUTF8": "1", **(spec.env or {})},
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = (await session.list_tools()).tools
        for tool in tools:
            self._route[tool.name] = session
            description = tool.description or ""
            self._tool_meta.append((tool.name, description))
            self._schemas.append(
                to_openai_function_schema(tool.name, description, tool.inputSchema)
            )

    # ---- 只读查询 -------------------------------------------------------

    def names(self) -> list[str]:
        """已连接工具名（插入序，稳定）。"""
        return list(self._route)

    def has(self, name: str) -> bool:
        return name in self._route

    def tool_schemas(self) -> list[dict]:
        """全部 MCP 工具的 OpenAI function schema（供 LLM tools 参数）。"""
        return list(self._schemas)

    def prompt_hint(self) -> str:
        """给研究 system prompt 追加的工具使用说明（教模型何时调这些工具）。

        MCP 工具的结果是「补充资料」（非网页搜索来源），强调不需要引用其 URL——
        与 notes 落库 / 报告呈现的语义保持一致。空连接返回空串（禁用路径不变）。
        """
        if not self._tool_meta:
            return ""
        lines = [
            "可用 MCP 辅助工具（stdio 子进程只读；结果仅供补充参考，不是网页搜索来源，"
            "报告里不需要为它们标注 [n] 引用）："
        ]
        for name, description in self._tool_meta:
            one = description.splitlines()[0] if description else ""
            if len(one) > 100:
                one = one[:100] + "…"
            lines.append(f"- {name}: {one}")
        return "\n".join(lines)

    # ---- 工具调用 -------------------------------------------------------

    async def call(self, name: str, arguments: dict) -> str:
        """调用 MCP 工具并把 CallToolResult 压成文本返回（未知工具抛 ValueError）。"""
        session = self._route.get(name)
        if session is None:
            raise ValueError(f"MCP 工具不存在: {name!r}")
        result = await session.call_tool(name, arguments or {})
        return call_result_to_text(result)


def build_mcp_specs(memory_db_path: str) -> list[MCPServerSpec]:
    """固定注册表：当前开放的 MCP server。

    - memory：把「用户历史研究」作为只读工具暴露（需 MEMORY_DB_PATH 绝对路径，
      子进程 cwd 与父进程不同，相对路径会指错位置）。
    - papers：arXiv 检索（无 key、无额外环境）。
    """
    return [
        MCPServerSpec(
            name="memory",
            module="knowledge_pilot.mcp.servers.memory",
            env={"MEMORY_DB_PATH": os.path.abspath(memory_db_path)},
        ),
        MCPServerSpec(
            name="papers",
            module="knowledge_pilot.mcp.servers.papers",
            env=None,
        ),
    ]
