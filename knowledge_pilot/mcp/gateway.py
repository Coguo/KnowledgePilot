"""MCP 网关：Agent 作为官方 `mcp` SDK 的 **client**，连接一组 stdio server。

本模块 **import-safe**：真实 stdio 连接下沉到 `StdioConnector`（`import mcp` 只在
`StdioConnector.connect()` 方法内执行），因此未装 mcp 也能 import 本文件来单测
网关的编排逻辑（连接失败跳过 / 断线重连 / 快照重建）。`mcp/__init__.py` 保持纯净，
调用方显式 `from knowledge_pilot.mcp.gateway import MCPGateway`。

生命周期（Phase 8 改造）：从「每请求 `async with` 现开现关」改为「可跨请求复用」——
新增 async `connect()` / `refresh()` / `aclose()`，常驻实例由 `mcp/runtime.py`
进程级单例持有；`__aenter__`/`__aexit__` 委托它们保持兼容。对调用方暴露的统一接口
不变，仍是 5 个方法：
    names() / has(name) / tool_schemas() / prompt_hint() / async call(name, args)
——mcp v1→v2 升级的爆炸半径仍圈在 StdioConnector 与 convert.call_result_to_text 里。

**常驻任务模型（Phase 8 关键约束）**：所有连接的进出（stdio 子进程握手与关闭）都收进
一个自持的长驻任务 `_serve`，调用方只往命令队列投递请求并 await Future，绝不亲自
进出这些上下文。原因是 mcp 的 `stdio_client` 内部用 anyio 的 task group，而 anyio 的
cancel scope **只能在进入它的那个任务里退出**；若直接在 SSE 请求任务里 connect()，
请求结束（Starlette 的 StreamingResponse 退出自己的 task group）时会抛
"Attempted to exit a cancel scope that isn't the current tasks's current cancel
scope"——生产上表现为整个 SSE 请求 500。串行化也随此天然成立：单消费者队列 = 同一
时刻只执行一个工具调用（读工具毫秒级，无需额外锁）；只读快照方法
（names/has/tool_schemas/prompt_hint）不参与队列。
"""

import asyncio
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Protocol

from knowledge_pilot.llm.errors import is_connection_lost
from knowledge_pilot.mcp.convert import call_result_to_text, to_openai_function_schema


@dataclass
class MCPServerSpec:
    """一个 MCP stdio server 的启动描述（固定注册表，新增 server = 加一行）。"""

    name: str  # 诊断用（memory / papers），不参与工具路由
    module: str  # "knowledge_pilot.mcp.servers.memory"，以 `python -m` 启动
    env: dict | None = None  # 注入子进程的额外环境变量（如 MEMORY_DB_PATH）


class SessionHandle(Protocol):
    """一个已连接的 server 会话（连接器产出），对网关屏蔽 mcp 协议细节。

    - list_tools() 同步返回 (name, description, inputSchema) 列表（连接时就已缓存），
      供网关离线重建路由快照（不需要二次协议调用）。
    - call_tool() 直接返回给 LLM 的文本（转换已下沉到实现内）。
    - aclose() 结束会话并让 stdio 子进程自然退出。
    """

    def list_tools(self) -> list[tuple[str, str, object]]:
        ...

    async def call_tool(self, name: str, arguments: dict) -> str:
        ...

    async def aclose(self) -> None:
        ...


class StdioConnector:
    """默认连接器：以 `python -m <module>` 拉起 stdio 子进程并握手。

    `import mcp` 系列全部在 connect() 方法内完成（本类顶层 import-safe）。
    子进程生命周期由返回的 _StdioSession 自持的 AsyncExitStack 管理：aclose() 先关
    stdio 读写流（mcp SDK 通过管道 EOF 让子进程自然退出），不直接 terminate（Windows
    下 taskkill 不可靠）。Windows 中文编码：env 显式全量传递并补 PYTHONUTF8=1。

    **调用约束**：connect()/aclose() 必须在同一个任务里成对调用（anyio cancel scope
    要求）；MCPGateway 通过常驻任务模型保证这一点。
    """

    async def connect(self, spec: MCPServerSpec) -> SessionHandle:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", spec.module],
            # 显式全量 env 并补 PYTHONUTF8：老 SDK env 整体替换、新 SDK 合并，
            # 显式完整 env 两者都安全；PYTHONUTF8=1 保证中文子进程 stdout 编码正确。
            env={**os.environ, "PYTHONUTF8": "1", **(spec.env or {})},
        )
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = (await session.list_tools()).tools
        return _StdioSession(stack, session, tools)


class _StdioSession:
    """真实 stdio 会话：持有 AsyncExitStack + mcp ClientSession，工具集连接时缓存。"""

    def __init__(self, stack: AsyncExitStack, session, tools) -> None:
        self._stack = stack
        self._session = session
        self._tools = [
            (tool.name, tool.description or "", tool.inputSchema) for tool in tools
        ]

    def list_tools(self) -> list[tuple[str, str, object]]:
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._session.call_tool(name, arguments or {})
        return call_result_to_text(result)

    async def aclose(self) -> None:
        await self._stack.aclose()


class MCPGateway:
    """聚合一组 stdio server 的工具，对外呈现统一的路由与 schema 列表。

    常驻复用语义（Phase 8）：
    - connect()：逐个 spec 连接，单个失败只告警 stderr 跳过（MCP 是可选增强，绝不
      因某个 server 挂掉整个研究）；已连接的 spec 不重复连（幂等）。
    - refresh()：只对「从未连上/已断开」的 spec 补连，带冷却限流，绝不每请求狂重连。
    - call()：工具执行失败若判定为「连接丢失」→ 自动重连一次后重试；仍失败则上抛
      （上层会兜底成可读文本）。非连接类错误原样上抛。
    - aclose()：关闭所有会话并让子进程退出（幂等）。

    上述每个方法都只是「投递命令 + await 结果」，真正的连接进出全部发生在常驻任务
    `_serve` 内（见模块 docstring「常驻任务模型」）。
    """

    def __init__(
        self,
        specs: list[MCPServerSpec],
        *,
        connector: SessionHandle | None = None,
        reconnect_attempts: int = 2,
        refresh_cooldown: float = 5.0,
    ) -> None:
        self._specs = list(specs)
        self._connector = connector if connector is not None else StdioConnector()
        self._reconnect_attempts = max(1, reconnect_attempts)
        self._refresh_cooldown = refresh_cooldown
        self._handles: dict[int, SessionHandle] = {}  # spec index → 活跃会话
        self._last_attempt: dict[int, float] = {}  # spec index → 上次连接尝试时刻
        self._route: dict[str, int] = {}  # 工具名 → spec index（查询快照）
        self._schemas: list[dict] = []  # OpenAI function schema（并入 ALL_TOOLS）
        self._tool_meta: list[tuple[str, str]] = []  # (name, description) 供 prompt_hint
        # 常驻任务状态：连接进出全部在 _serve 内执行（见模块 docstring）。
        self._shutting_down = False  # aclose 期间置位：新的提交等它结束再启新任务
        self._queue: asyncio.Queue | None = None  # (op, payload, future)
        self._task: asyncio.Task | None = None

    # ---- 生命周期（对外：只投递命令，不碰任何连接上下文）-----------------

    async def __aenter__(self) -> "MCPGateway":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def connect(self) -> "MCPGateway":
        """启动常驻任务并完成首轮连接握手。幂等。"""
        await self._submit("connect")
        return self

    async def refresh(self) -> None:
        """补连「从未连上/已断开」的 spec（冷却限流在常驻任务内判定）。"""
        if self._task is None:
            return
        await self._submit("refresh")

    async def aclose(self) -> None:
        """关闭所有会话、收掉常驻任务（幂等）。

        关闭动作（含 anyio cancel scope 的退出）在常驻任务内执行——与调用方所在任务
        无关，因此 lifespan shutdown 或测试 teardown 都能安全调用。
        """
        if self._task is None:
            return
        self._shutting_down = True
        try:
            await self._submit("close", during_shutdown=True)
        finally:
            task = self._task
            self._task = None
            self._queue = None
            self._shutting_down = False
            if task is not None:
                try:
                    await task
                except BaseException:  # noqa: BLE001 — 清理阶段尽力而为
                    pass

    # ---- 只读查询（不参与队列：读的是快照）--------------------------------

    def names(self) -> list[str]:
        """已连接工具名（连接成功序，稳定）。"""
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

    # ---- 工具调用（对外）--------------------------------------------------

    async def call(self, name: str, arguments: dict) -> str:
        """调用 MCP 工具并把结果压成文本返回（未知工具抛 ValueError）。

        连接丢失 → 常驻任务内自动重连一次后重试（工具只读，重试语义安全）；仍失败或
        非连接类错误原样上抛（上层 loop._dispatch_tool 会兜底成可读文本，不崩研究）。
        """
        return await self._submit("call", (name, arguments))

    # ---- 常驻任务：所有连接的进出都在这里 ----------------------------------

    async def _submit(
        self, op: str, payload: object = None, *, during_shutdown: bool = False
    ) -> object:
        """投递一个命令给常驻任务并 await 其结果。

        任务的创建/续跑在关闭窗口之外完成——否则 teardown（cancel scope 仍活跃）与
        新任务（另一个 scope）会交错，引发 "cancel scope 不属于当前任务" 连锁报错。
        """
        loop = asyncio.get_running_loop()
        while self._shutting_down and not during_shutdown:
            await asyncio.sleep(0)  # 让出给正在收尾的 aclose，等它重置状态
        if self._task is None or self._task.done():
            self._queue = asyncio.Queue()
            self._task = loop.create_task(self._serve())
        future: asyncio.Future = loop.create_future()
        assert self._queue is not None
        self._queue.put_nowait((op, payload, future))
        await future  # 调用方被取消（客户端断开）时这里抛 CancelledError，队列项自会被跳过
        return future.result()

    async def _serve(self) -> None:
        """常驻任务：独占消费命令队列，连接握手/关闭/重连全部在本任务内 await。

        单消费者天然串行 → 同一时刻只执行一个工具调用（读工具毫秒级，可接受）。
        """
        queue = self._queue
        assert queue is not None
        while True:
            op, payload, future = await queue.get()

            if op == "close":
                try:
                    await self._close_all()
                finally:
                    # 无论如何都要兑现调用方的等待：anyio scope 收尾可能抛出 CancelledError
                    # （BaseException），若让它穿透，aclose()/lifespan 收尾会永久挂住。
                    if not future.done():
                        future.set_result(None)
                    # 关闭后仍在队列里的命令一律明确失败（而不是让调用方永远挂住）。
                    while not queue.empty():
                        _, _, pending = queue.get_nowait()
                        if not pending.done():
                            pending.set_exception(RuntimeError("MCP 网关已关闭"))
                return

            if future.cancelled():  # 调用方已放弃（客户端断开）→ 不做无用功
                continue

            try:
                if op == "connect":
                    await self._connect_specs()
                    result = None
                elif op == "refresh":
                    await self._refresh_specs()
                    result = None
                elif op == "call":
                    result = await self._call_tool(*payload)  # type: ignore[misc]
                else:
                    raise ValueError(f"未知的 MCP 网关操作: {op!r}")
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 — 把错误交给调用方，任务本身不崩
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)

    async def _connect_specs(self) -> None:
        """首轮连接：逐个 spec 握手，单个失败告警跳过不抛。已连接的跳过（幂等）。"""
        for idx, spec in enumerate(self._specs):
            if idx in self._handles:
                continue
            self._last_attempt[idx] = time.time()
            try:
                self._handles[idx] = await self._connector.connect(spec)
            except Exception as exc:  # noqa: BLE001 — 可选增强：失败跳过而非上抛
                print(f"[mcp] server {spec.module} 启动失败，已跳过：{exc}", file=sys.stderr)
        self._rebuild()

    async def _refresh_specs(self) -> None:
        """补连「从未连上/已断开」的 spec，带冷却限流。

        冷却期内不重试，避免某个 server 持续故障时每个请求都触发 spawn。
        """
        now = time.time()
        for idx, spec in enumerate(self._specs):
            if idx in self._handles:
                continue
            if now - self._last_attempt.get(idx, 0.0) < self._refresh_cooldown:
                continue
            self._last_attempt[idx] = now
            try:
                self._handles[idx] = await self._connector.connect(spec)
            except Exception as exc:  # noqa: BLE001
                print(f"[mcp] server {spec.module} 补连失败，已跳过：{exc}", file=sys.stderr)
        self._rebuild()

    async def _close_all(self) -> None:
        """关闭所有会话（结束 stdio 子进程）并清空访问快照。"""
        for idx in self._close_order():
            handle = self._handles.pop(idx, None)
            if handle is not None:
                await self._close_handle(handle)
        self._last_attempt.clear()
        self._rebuild()

    def _close_order(self) -> list[int]:
        """活跃 spec 索引，按**进入顺序的逆序**（anyio cancel scope 必须 LIFO 退出）。

        每个 stdio 会话在常驻任务里进入一个 anyio cancel scope（`stdio_client` 内部用
        task group），它们叠在**同一个任务**的 scope 栈上。若按进入顺序关，退出栈底的
        scope 时会抛 "Attempted to exit a cancel scope that isn't the current tasks's
        current cancel scope"，scope 栈从此错乱（错误被吞掉时表现为漏杀子进程）。
        """
        return list(reversed(list(self._handles)))

    async def _close_handle(self, handle: SessionHandle) -> None:
        """尽力关闭单个会话：失败不得拖累其余会话（否则会漏杀 stdio 子进程）。

        注意 `CancelledError` 是 BaseException，`except Exception` 挡不住它。
        """
        try:
            await handle.aclose()
        except BaseException as exc:  # noqa: BLE001 — 一个失败不能拖累其余会话
            print(
                f"[mcp] 关闭会话时被中断（继续关闭其余）：{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    async def _call_tool(self, name: str, arguments: dict) -> str:
        """在常驻任务内执行一次工具调用（含断线重连重试）。"""
        idx = self._route.get(name)
        if idx is None:
            raise ValueError(f"MCP 工具不存在: {name!r}")
        handle = self._handles.get(idx)
        if handle is None:
            handle = await self._reconnect_spec(idx)
            if handle is None:
                raise RuntimeError(f"MCP 工具 {name!r} 的 server 连接不可用")
        try:
            return await handle.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 — 判定是否为连接丢失后决定重连
            if not is_connection_lost(exc):
                raise
            await self._reconnect_spec(idx)
            handle = self._handles.get(idx)
            if handle is None:
                raise
            return await handle.call_tool(name, arguments)

    async def _reconnect_spec(self, idx: int) -> SessionHandle | None:
        """重连单个 spec（关旧会话 → 有限次尝试），成功后重建快照。

        串行化的任务模型下不需要退避等待：重试期间阻塞的是队列本身，sleep 只会拖慢
        其它请求而无实际收益（故障 server 由 refresh 的冷却限流兜底）。

        为保持「`_handles` 顺序 == anyio cancel scope 栈顺序」的不变量（见
        `_close_order`），只关目标本身是不够的——目标之上的会话必须先按 LIFO 关掉，
        重连目标后再按原顺序补回；否则 scope 栈会错位。
        """
        order = list(self._handles)
        victims = order[order.index(idx):] if idx in order else []  # 目标 + 其后进入的
        for victim in reversed(victims):
            handle = self._handles.pop(victim, None)
            if handle is not None:
                await self._close_handle(handle)
        handle = await self._connect_one(idx)
        for victim in victims[1:]:  # 按原顺序补回，维持 dict 顺序 == scope 栈顺序
            await self._connect_one(victim)
        self._rebuild()
        return handle

    async def _connect_one(self, idx: int) -> SessionHandle | None:
        """连接单个 spec（最多 reconnect_attempts 次），成功则登记并返回会话。"""
        spec = self._specs[idx]
        for _ in range(self._reconnect_attempts):
            try:
                handle = await self._connector.connect(spec)
                self._handles[idx] = handle
                return handle
            except Exception:  # noqa: BLE001
                continue
        print(f"[mcp] server {spec.module} 重连失败，已放弃", file=sys.stderr)
        return None

    def _rebuild(self) -> None:
        """从活跃会话重展平路由/工具 schema/提示元数据（连接或重连后调用）。"""
        route: dict[str, int] = {}
        schemas: list[dict] = []
        meta: list[tuple[str, str]] = []
        for idx, handle in self._handles.items():
            if handle is None:
                continue
            for tool_name, description, input_schema in handle.list_tools():
                route[tool_name] = idx
                meta.append((tool_name, description))
                schemas.append(to_openai_function_schema(tool_name, description, input_schema))
        self._route = route
        self._schemas = schemas
        self._tool_meta = meta


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
