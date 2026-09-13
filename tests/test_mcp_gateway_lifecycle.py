"""MCP 网关常驻生命周期（Phase 8）：复用 / 补连 / 断线重连 / 并发串行 / 失败降级。

全程不 import mcp —— 注入**假 connector/会话**驱动网关编排逻辑；真实 stdio 子进程连接
在 B 轨（tests/test_mcp_servers_stdio.py）验。

测试用同步函数包 `asyncio.run`（而非 async def），使这些用例在未装 pytest-asyncio 的
离线沙箱也能真正执行；每个用例的全部异步动作都放进同一个 `asyncio.run`（Lock 绑定
事件循环，跨 loop 复用会报错）。
"""

import asyncio

import httpx
import pytest

from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.llm.protocol import StreamChunk
from knowledge_pilot.mcp.gateway import MCPGateway, MCPServerSpec
from knowledge_pilot.mcp.runtime import MCPRuntime, specs_fingerprint
from knowledge_pilot.search.stub import StubSearchProvider

_MEM = MCPServerSpec(name="memory", module="mod_memory", env={"MEMORY_DB_PATH": "/abs/m.db"})
_PAPERS = MCPServerSpec(name="papers", module="mod_papers", env=None)


# ---- 假 connector / 会话 -------------------------------------------------

class FakeSession:
    """假 stdio 会话：固定工具集 + 脚本化异常（每次 call 先弹一个异常）。"""

    def __init__(self, tools, responses, call_errors, delay=0.0, close_log=None) -> None:
        self._tools = list(tools)
        self._responses = dict(responses)
        self._errors = call_errors  # 共享列表：跨重连后的会话继续消费同一脚本
        self._delay = delay
        self._close_log = close_log if close_log is not None else []
        self._close_exc = None  # 测试可注入：模拟 anyio scope 收尾抛 BaseException
        self.label = "?"  # 由 FakeConnector 填 spec.module，供关闭顺序断言
        self.calls = 0
        self.closed = False
        self.active = 0
        self.max_active = 0

    def list_tools(self):
        return list(self._tools)

    async def call_tool(self, name, arguments):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._errors:
                exc = self._errors.pop(0)
                if exc is not None:
                    raise exc
            return self._responses.get(name, f"result:{name}")
        finally:
            self.active -= 1

    async def aclose(self):
        self.closed = True
        self._close_log.append(self.label)
        if self._close_exc is not None:
            raise self._close_exc


class FakeConnector:
    def __init__(self, *, tools_by_module=None, responses=None, call_errors=None,
                 fail_modules=None):
        self.connect_log: list[str] = []
        # 所有会话共享一份关闭日志 → 记录 aclose 的**顺序**（anyio cancel scope LIFO）。
        self.close_log: list[str] = []
        self.sessions: list[FakeSession] = []
        # 测试可中途增删以模拟连接失败/恢复（refresh 的冷却/补连语义靠它驱动）。
        self.fail_modules: set[str] = set(fail_modules or ())
        self._tools_by_module = tools_by_module or {}
        self._responses = responses or {}
        self._call_errors = call_errors if call_errors is not None else []

    async def connect(self, spec):
        self.connect_log.append(spec.module)
        if spec.module in self.fail_modules:
            raise RuntimeError(f"cannot start {spec.module}")
        tools = self._tools_by_module.get(
            spec.module, [(f"tool_{spec.module}", "desc", {"type": "object"})]
        )
        session = FakeSession(
            tools, self._responses, self._call_errors, close_log=self.close_log
        )
        session.label = spec.module
        self.sessions.append(session)
        return session


# ---- connect 复用 / 失败跳过 / 禁用路径 ---------------------------------

def test_connect_is_idempotent():
    connector = FakeConnector()
    gw = MCPGateway([_MEM], connector=connector)

    async def main():
        await gw.connect()
        await gw.connect()  # 第二次不应重连
        return gw.names()

    assert asyncio.run(main()) == ["tool_mod_memory"]
    assert connector.connect_log == ["mod_memory"]  # 只 spawn 一次


def test_connect_skips_failing_spec():
    connector = FakeConnector(fail_modules={"mod_papers"})

    async def main():
        gw = MCPGateway([_MEM, _PAPERS], connector=connector)
        await gw.connect()
        return gw.names()

    assert asyncio.run(main()) == ["tool_mod_memory"]  # 只有成功那个


def test_all_specs_fail_is_disabled_path():
    connector = FakeConnector(fail_modules={"mod_memory", "mod_papers"})

    async def main():
        gw = MCPGateway([_MEM, _PAPERS], connector=connector)
        await gw.connect()
        with pytest.raises(ValueError):
            await gw.call("search_memory", {})
        return gw.names(), gw.has("search_memory"), gw.tool_schemas(), gw.prompt_hint()

    names, has, schemas, hint = asyncio.run(main())
    # 全失败 = 与「未启用 MCP」完全一致（空工具集、空提示）。
    assert names == [] and has is False and schemas == [] and hint == ""


# ---- refresh 冷却限流 ---------------------------------------------------

def test_refresh_blocked_by_cooldown():
    connector = FakeConnector(fail_modules={"mod_memory"})

    async def main():
        gw = MCPGateway([_MEM], connector=connector, refresh_cooldown=1000.0)
        await gw.connect()  # 失败一次
        await gw.refresh()  # 冷却期内 → 不重试
        return gw.names()

    assert asyncio.run(main()) == []
    assert connector.connect_log == ["mod_memory"]  # 没有第二次 spawn


def test_refresh_reconnects_after_cooldown_and_recovers():
    connector = FakeConnector(fail_modules={"mod_memory"})

    async def main():
        gw = MCPGateway([_MEM], connector=connector, refresh_cooldown=0.0)
        await gw.connect()  # 失败
        connector.fail_modules.discard("mod_memory")  # server 恢复
        await gw.refresh()
        return gw.names()

    assert asyncio.run(main()) == ["tool_mod_memory"]
    assert connector.connect_log == ["mod_memory", "mod_memory"]


# ---- call：断线重连 / 非连接错 / 未知工具 -------------------------------

def test_call_reconnects_after_connection_lost():
    connector = FakeConnector(call_errors=[httpx.ConnectError("pipe closed")])

    async def main():
        gw = MCPGateway([_MEM], connector=connector)
        await gw.connect()
        return await gw.call("tool_mod_memory", {}), gw.names()

    result, names = asyncio.run(main())
    assert result == "result:tool_mod_memory"  # 重连后成功
    assert connector.connect_log == ["mod_memory", "mod_memory"]  # 重连一次
    assert names == ["tool_mod_memory"]  # 快照重建后仍在


def test_call_raises_when_reconnect_fails():
    connector = FakeConnector(call_errors=[httpx.ConnectError("pipe closed")])

    async def main():
        gw = MCPGateway([_MEM], connector=connector, reconnect_attempts=2)
        await gw.connect()
        connector.fail_modules.add("mod_memory")  # 从此连不上
        await gw.call("tool_mod_memory", {})

    # 重连仍失败 → 原样上抛连接错（上层 loop._dispatch_tool 兜成可读文本）。
    with pytest.raises(httpx.ConnectError):
        asyncio.run(main())
    assert connector.connect_log.count("mod_memory") == 3  # 初次 + 2 次重连尝试


def test_call_non_connection_error_propagates_without_reconnect():
    connector = FakeConnector(call_errors=[ValueError("bad arguments")])

    async def main():
        gw = MCPGateway([_MEM], connector=connector)
        await gw.connect()
        await gw.call("tool_mod_memory", {})

    with pytest.raises(ValueError):
        asyncio.run(main())
    assert connector.connect_log == ["mod_memory"]  # 非连接错不重连


def test_call_unknown_tool_raises():
    connector = FakeConnector()

    async def main():
        gw = MCPGateway([_MEM], connector=connector)
        await gw.connect()
        await gw.call("nope", {})

    with pytest.raises(ValueError):
        asyncio.run(main())


def test_concurrent_calls_are_serialized():
    connector = FakeConnector()

    async def main():
        gw = MCPGateway([_MEM], connector=connector)
        await gw.connect()
        connector.sessions[0]._delay = 0.02
        await asyncio.gather(
            gw.call("tool_mod_memory", {}), gw.call("tool_mod_memory", {})
        )
        return connector.sessions[0].max_active

    assert asyncio.run(main()) == 1  # serving task 串行取队列，同一时刻只跑一个调用


# ---- 关闭顺序 / 收尾异常：anyio cancel scope 栈的两条不变量 ----------------
#
# mcp 的 stdio_client 内部用 anyio.create_task_group()，每个已连 server 都在
# **serving task** 的 cancel scope 栈上占一层。因此：
#   1) 必须 LIFO 退出——按进入顺序 close 会破坏 scope 栈，抛
#      "Attempted to exit a cancel scope that isn't the current tasks's current
#      cancel scope"，并让 CancelledError 逃逸、杀掉 serving task；
#   2) 收尾异常（CancelledError 是 BaseException，except Exception 拦不住）不得
#      逃逸出 _serve，否则调用方 await 的 future 永不结束 → aclose 挂死。
# 下面两条用例分别锁死这两点（本次挂起事故的根因）。


def test_close_is_reverse_of_connect_order():
    """aclose 按进入顺序的**逆序**关闭会话。"""
    connector = FakeConnector()

    async def main():
        gw = MCPGateway([_MEM, _PAPERS], connector=connector)
        await gw.connect()
        await gw.aclose()
        return connector.close_log, [s.closed for s in connector.sessions]

    close_log, closed = asyncio.run(main())
    assert close_log == ["mod_papers", "mod_memory"]  # 后进先出
    assert closed == [True, True]  # 两个都真关了


def test_reconnect_reestablishes_later_sessions_in_order():
    """补连靠前的 spec：先逆序拆掉其后所有会话，再按原顺序重建。

    只拆「失败的那个」会让后续会话的 scope 在栈上乱序（失败者被移出、其余原地），
    故重连时连同其后会话一起拆/一起补。
    """
    connector = FakeConnector(call_errors=[httpx.ConnectError("pipe closed")])

    async def main():
        gw = MCPGateway([_MEM, _PAPERS], connector=connector)
        await gw.connect()
        await gw.call("tool_mod_memory", {})  # 触发 mod_memory 断线重连
        await gw.aclose()
        return list(connector.connect_log), list(connector.close_log)

    connect_log, close_log = asyncio.run(main())
    assert connect_log == ["mod_memory", "mod_papers", "mod_memory", "mod_papers"]
    assert close_log == ["mod_papers", "mod_memory", "mod_papers", "mod_memory"]


def test_aclose_survives_base_exception_from_a_session_close():
    """某个会话收尾抛 BaseException → 不逃逸、不挂死，其余会话照常关完。

    用 asyncio.CancelledError 而非 Exception 子类：只有它才能复现
    except Exception 漏网 → serving task 被杀 → aclose 永久挂起 的原始故障。
    """
    connector = FakeConnector()

    async def main():
        gw = MCPGateway([_MEM, _PAPERS], connector=connector)
        await gw.connect()
        # 第一个被关的 mod_papers 抛错，mod_memory 仍须被关。
        connector.sessions[1]._close_exc = asyncio.CancelledError("scope teardown")
        await asyncio.wait_for(gw.aclose(), 5)  # 挂起则 TimeoutError
        return connector.close_log, [s.closed for s in connector.sessions]

    close_log, closed = asyncio.run(main())
    assert close_log == ["mod_papers", "mod_memory"]
    assert closed == [True, True]


# ---- runtime：复用 / 指纹重建 / 关闭 ------------------------------------

def test_runtime_ensure_reuses_gateway():
    connector = FakeConnector()

    async def main():
        rt = MCPRuntime(connector=connector)
        first = await rt.ensure([_MEM, _PAPERS])
        second = await rt.ensure([_MEM, _PAPERS])
        await rt.aclose()
        return first is second, connector.connect_log

    same, log = asyncio.run(main())
    assert same is True  # 同 specs → 同一常驻网关（跨请求复用）
    assert log == ["mod_memory", "mod_papers"]  # 只 spawn 一轮


def test_runtime_rebuilds_on_fingerprint_change():
    connector = FakeConnector()
    changed = MCPServerSpec(name="memory", module="mod_memory", env={"MEMORY_DB_PATH": "/new/m.db"})

    async def main():
        rt = MCPRuntime(connector=connector)
        first = await rt.ensure([_MEM])
        old_session = connector.sessions[0]
        second = await rt.ensure([changed])  # 指纹变化 → 关旧连新
        await rt.aclose()
        return first is second, old_session.closed, connector.connect_log

    same, old_closed, log = asyncio.run(main())
    assert same is False
    assert old_closed is True  # 旧网关子进程被关
    assert log == ["mod_memory", "mod_memory"]


def test_runtime_aclose_is_idempotent_and_closes_sessions():
    connector = FakeConnector()

    async def main():
        rt = MCPRuntime(connector=connector)
        await rt.ensure([_MEM])
        session = connector.sessions[0]
        await rt.aclose()
        await rt.aclose()  # 幂等
        return session.closed, rt._gateway

    closed, gateway = asyncio.run(main())
    assert closed is True
    assert gateway is None


def test_runtime_refresh_is_noop_before_ensure_and_delegates_after():
    connector = FakeConnector(fail_modules={"mod_memory"})

    async def main():
        rt = MCPRuntime(connector=connector, cooldown_seconds=0.0)
        await rt.refresh()  # 尚未 ensure → 无操作
        before = list(connector.connect_log)
        await rt.ensure([_MEM])  # 失败
        connector.fail_modules.discard("mod_memory")  # 恢复
        await rt.refresh()  # 委托网关补连（冷却 0）
        names = rt._gateway.names()
        await rt.aclose()
        return before, connector.connect_log, names

    before, log, names = asyncio.run(main())
    assert before == []  # ensure 前 refresh 无动作
    assert log == ["mod_memory", "mod_memory"]
    assert names == ["tool_mod_memory"]


def test_specs_fingerprint_is_stable_and_env_order_independent():
    a = MCPServerSpec(name="m", module="mod", env={"A": "1", "B": "2"})
    b = MCPServerSpec(name="m", module="mod", env={"B": "2", "A": "1"})
    assert specs_fingerprint([a]) == specs_fingerprint([b])
    assert specs_fingerprint([a]) != specs_fingerprint([_MEM])


# ---- loop：MCP 工具失败不崩整个研究 --------------------------------------

class _BoomGateway:
    """鸭子 MCPGateway：声明一个工具但调用必抛（模拟子进程挂掉）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def names(self):
        return ["search_memory"]

    def has(self, name):
        return name == "search_memory"

    def tool_schemas(self):
        return [{
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": "历史研究检索",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    def prompt_hint(self):
        return "hint"

    async def call(self, name, arguments):
        self.calls.append(name)
        raise RuntimeError("server down")


class _ScriptedLLM:
    """最小假 LLM（只实现 run_research 需要的 stream_chat），避免 tests.fakes 拉 openai。"""

    model = "fake"

    def __init__(self, script) -> None:
        self.script = script
        self.calls = 0
        self.seen_tools: list = []

    async def stream_chat(self, messages, tools=None):
        self.seen_tools.append(tools)
        content_parts, tool_calls = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        for part in content_parts:
            yield StreamChunk(content_delta=part)
        for index, tc in enumerate(tool_calls):
            yield StreamChunk(tool_call_delta={
                "index": index, "id": f"call_{index}", "name": tc["name"],
                "arguments": tc["arguments"],
            })


def test_loop_survives_mcp_tool_failure_with_readable_text():
    llm = _ScriptedLLM(script=[
        ([], [{"name": "search_memory", "arguments": "{}"}]),
        (["最终回答。"], []),
    ])
    gateway = _BoomGateway()
    extra: list[tuple[str, str]] = []

    async def main():
        return [
            e async for e in run_research(
                "历史问题", llm=llm, search=StubSearchProvider(),
                mcp=gateway, on_extra_tool_result=lambda n, t: extra.append((n, t)),
            )
        ]

    events = asyncio.run(main())
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["ToolCallEvent", "ToolResultEvent", "TokenEvent", "DoneEvent"]

    summary = events[1].summary
    assert "暂不可用" in summary and "RuntimeError" in summary  # 失败转可读文本
    assert events[-1].content == "最终回答。"  # 研究照常完成
    assert gateway.calls == ["search_memory"]
    assert extra == []  # 失败结果不进 notes（只收真实结果）
