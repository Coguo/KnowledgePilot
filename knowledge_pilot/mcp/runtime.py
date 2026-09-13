"""MCP 常驻运行时（Phase 8）：进程级单例持有跨请求复用的 MCPGateway。

设计要点：
- **懒连接**：首个真正需要 MCP 的请求才 spawn（ASGITransport 测试不触发 FastAPI
  lifespan；启动即拉起子进程也违背「默认关零开销」理念）。uvicorn 单 worker =
  单进程单事件循环 → 网关对象与 stdio 子进程挂在同一 loop 上跨请求存活；多 worker
  各进程独立 runtime，天然隔离。
- **指纹**：specs 变化（如 MEMORY_DB_PATH 改变）→ 先 aclose 旧网关再重连，
  避免按旧路径/旧配置复用已死的子进程。
- **refresh**：只补连「从未连上/已断开」的 server，带冷却限流（委托网关），
  绝不因某个 server 持续故障而在每个请求都触发 spawn。
- **关闭**：FastAPI lifespan shutdown 与测试 teardown 调 aclose()/reset()。

本模块不 import mcp（只 import gateway.py），A 轨可测：测试注入假 connector 即可
断言「跨请求复用 / 指纹重建 / 禁用即无 spawn」。
"""

import asyncio

from knowledge_pilot.mcp.gateway import MCPGateway, MCPServerSpec


def specs_fingerprint(specs: list[MCPServerSpec]) -> tuple:
    """specs 的稳定指纹：module + 排序后的 env 键值对（env 已由 build_mcp_specs 绝对化）。"""
    parts = []
    for spec in specs:
        env = () if spec.env is None else tuple(sorted(spec.env.items()))
        parts.append((spec.module, env))
    return tuple(parts)


class MCPRuntime:
    """进程级 MCP 网关单例的持有者。所有方法幂等、可重入。"""

    def __init__(
        self,
        *,
        connector: object | None = None,
        cooldown_seconds: float = 5.0,
    ) -> None:
        self._gateway: MCPGateway | None = None
        self._fingerprint: tuple | None = None
        self._connector = connector  # 注入假连接器（A 轨测试用）；None → 真实 StdioConnector
        self._cooldown = cooldown_seconds
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def ensure(self, specs: list[MCPServerSpec]) -> MCPGateway:
        """返回常驻网关：首用或指纹变化时连接，否则直接复用已有（不重连）。"""
        fingerprint = specs_fingerprint(specs)
        async with self._get_lock():
            if self._gateway is not None and fingerprint == self._fingerprint:
                return self._gateway
            if self._gateway is not None:
                await self._gateway.aclose()
            gateway = MCPGateway(
                specs,
                connector=self._connector,
                refresh_cooldown=self._cooldown,
            )
            await gateway.connect()
            self._gateway = gateway
            self._fingerprint = fingerprint
            return gateway

    async def refresh(self) -> None:
        """补连「从未连上/已断开」的 server（网关内带冷却限流）。"""
        if self._gateway is None:
            return
        await self._gateway.refresh()

    async def aclose(self) -> None:
        """关闭常驻网关（结束所有 stdio 子进程），幂等。"""
        async with self._get_lock():
            if self._gateway is not None:
                await self._gateway.aclose()
                self._gateway = None
                self._fingerprint = None
            # Lock 绑定首次使用它的事件循环（asyncio._LoopBoundMixin），而进程级单例
            # 会跨事件循环存活（如 pytest-asyncio 每个用例一个 loop）→ 复位，下次在新
            # 循环上重建，避免 "is bound to a different event loop"。
            self._lock = None

    async def reset(self) -> None:
        """aclose 的别名（测试 teardown / 异常态回收语义更明确的入口）。"""
        await self.aclose()


_runtime: MCPRuntime | None = None


def get_mcp_runtime() -> MCPRuntime:
    """进程级单例（api 层用它；测试用 reset() 复位到干净态）。"""
    global _runtime
    if _runtime is None:
        _runtime = MCPRuntime()
    return _runtime
