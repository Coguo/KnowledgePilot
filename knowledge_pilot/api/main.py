"""FastAPI 入口：Research Chat 的 SSE 端点。

启动：
    uvicorn knowledge_pilot.api.main:app --reload

Phase 9 起这里只留「研究入口」（`/api/chat`）+ 应用装配；学习图谱的全部路由在
`api/learning.py`，两者共用 `api/deps.py`（依赖装配）与 `api/sse.py`（帧编码）。
本模块 re-export 那两个模块的名字，保持既有 import 路径与测试的依赖覆盖不变。
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from knowledge_pilot.agent.events import ErrorEvent
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.api.deps import (
    ChatDeps,
    acquire_mcp_gateway,
    close_memory as _close_memory,
    close_rag as _close_rag,
    get_chat_deps,
)
from knowledge_pilot.api.learning import router as learning_router
from knowledge_pilot.api.sse import safe_error_text as _safe_error_text
from knowledge_pilot.api.sse import sse_frame as _sse_frame
from knowledge_pilot.config import clamp_rounds, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：关闭时清理常驻的 MCP 子进程。

    uvicorn 正常启动/关闭会跑 lifespan。ASGITransport（测试用）不跑 lifespan →
    常驻网关必须是**懒连接**（首个真实请求经 runtime.ensure 才 spawn），此处只管关闭。
    """
    yield
    from knowledge_pilot.mcp.runtime import get_mcp_runtime

    await get_mcp_runtime().aclose()


app = FastAPI(title="KnowledgePilot — AI Research Agent", version="0.1.0", lifespan=lifespan)
app.include_router(learning_router)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# 前端在 Phase 10 拆成了多文件(css/ + js/),需要一个静态路由来送它们。
#
# **只挂 WEB_DIR,绝不能挂项目根**:挂根等于把 `.env`(含 API key)与
# `data/learning.db`(含全部学习记录)变成可下载的 URL。
# `test_static_mount_does_not_expose_repo` 会用 `%2e%2e%2f` 路径钉住这条。
#
# 挂在 `/static` 而非 `/`:`/` 交给下面的 `index()`,未知的 `/api/*` 仍由 FastAPI
# 自己的 404 处理(挂到 `/` 会把它们变成 StaticFiles 的纯文本 404)。
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ---- 请求/响应模型 ---------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    # Phase 9：单次精准查询的轮数覆盖，两个字段都可选 → 老请求体行为逐字节不变。
    # 都会被钳到 [1, settings.agent_rounds_hard_cap]（见 clamp_rounds）。
    max_iterations: int | None = None  # 研究-评估条件循环上限
    max_tool_rounds: int | None = None  # 单次研究的工具调用轮次上限


# ---- 路由 -----------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.post("/api/chat")
async def chat(req: ChatRequest, deps: ChatDeps = Depends(get_chat_deps)) -> StreamingResponse:
    """以 SSE 流返回 Agent 事件（token / tool_call / tool_result / done）。"""

    def _runner_for(mcp):
        """按 agent_mode 构造研究 runner。MCP 只对 graph 模式生效（loop 是回归路径）。

        Phase 9：请求体可带 max_iterations / max_tool_rounds 做单次精准查询；缺省（None）
        时落到 .env 配置值，行为与旧版逐字节一致。请求值经 clamp_rounds 钳到硬上限。
        """
        cap = settings.agent_rounds_hard_cap
        max_iterations = clamp_rounds(
            req.max_iterations, settings.agent_max_iterations, cap
        )
        max_tool_rounds = clamp_rounds(
            req.max_tool_rounds, settings.agent_max_tool_rounds, cap
        )
        if settings.agent_mode == "graph":
            return run_research_graph(
                req.message,
                llm=deps.llm,
                search=deps.search,
                rag=deps.rag,
                max_iterations=max_iterations,
                memory=deps.memory,
                memory_top_k=settings.memory_top_k,
                checkpoint_db=settings.memory_checkpoint_db_path,
                kg_enabled=settings.kg_enabled,
                kg_hops=settings.kg_hops,
                mcp=mcp,
                max_tool_rounds=max_tool_rounds,
            )
        return run_research(
            req.message,
            llm=deps.llm,
            search=deps.search,
            rag=deps.rag,
            max_tool_rounds=max_tool_rounds,
        )

    async def event_stream():
        # Phase 8 MCP 常驻：默认关 → gateway=None，runner 与 Phase 5 完全一致。
        gateway = await acquire_mcp_gateway()

        try:
            async for event in _runner_for(gateway):
                yield _sse_frame(event)
        except Exception as exc:  # noqa: BLE001 — 流内异常必须变成一帧发给前端
            # 之前这里没有 except：异常直接穿出异步生成器，客户端拿到 200 + 截断的
            # body，前端一个错误都不显示 → 用户无法区分「模型挂了」与「还在跑」。
            # CancelledError 继承 BaseException，不会被这里吞掉，取消语义不变。
            yield _sse_frame(ErrorEvent(message=_safe_error_text(exc)))
        finally:
            _close_rag(deps.rag)
            _close_memory(deps.memory)
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 避免 nginx 缓冲影响流式
        },
    )


# ---- SSE 帧编码 ------------------------------------------------------
# 实现在 `api/sse.py`（与 `/api/learning/*` 共用）；本模块顶部 re-export 了
# `_sse_frame` / `_safe_error_text` 两个私有名，保持既有 import 路径可用。
