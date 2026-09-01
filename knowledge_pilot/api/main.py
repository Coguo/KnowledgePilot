"""FastAPI 入口：Research Chat 的 SSE 端点。

启动：
    uvicorn knowledge_pilot.api.main:app --reload
"""

import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from knowledge_pilot.agent.events import (
    DoneEvent,
    EvalEvent,
    MemoryEvent,
    PlanEvent,
    StatusEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.config import settings
from knowledge_pilot.llm.client import ChatClient, LLMClient
from knowledge_pilot.memory import create_memory_store
from knowledge_pilot.search import SearchProvider, create_search_provider

app = FastAPI(title="KnowledgePilot — AI Research Agent", version="0.1.0")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# ---- 依赖注入 -------------------------------------------------------

@dataclass
class ChatDeps:
    llm: LLMClient
    search: SearchProvider
    rag: object | None = None  # RAGPipeline，未启用/未安装时为 None
    memory: object | None = None  # ResearchMemoryStore，未启用时为 None


def get_chat_deps() -> ChatDeps:
    """组装本次请求的 LLM、搜索与 RAG 依赖。未配置密钥时给出清晰错误。"""
    if not settings.has_api_key:
        raise HTTPException(
            status_code=500,
            detail=(
                "未配置 DEEPSEEK_API_KEY：请复制 .env.example 为 .env，"
                "填入密钥后重启服务。"
            ),
        )
    try:
        search = create_search_provider(settings.search_provider, settings.tavily_api_key)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None

    # 先建 LLM：RAG 开启查询改写时，工厂需要同一个客户端做改写调用。
    llm = ChatClient(settings)

    rag = None
    if settings.rag_enabled:
        try:
            from knowledge_pilot.rag import create_rag_pipeline

            rag = create_rag_pipeline(settings, llm)
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "RAG 已启用但缺少依赖：请先运行 "
                    "pip install -e \".[rag]\" 后重启服务。"
                ),
            ) from exc

    # Phase 4 Memory：开启时每次请求建临时 store（数据在磁盘上持久化，请求结束关闭）。
    memory = None
    if settings.memory_enabled:
        memory = create_memory_store(settings.memory_db_path)

    return ChatDeps(
        llm=llm,
        search=search,
        rag=rag,
        memory=memory,
    )


# ---- 请求/响应模型 ---------------------------------------------------

class ChatRequest(BaseModel):
    message: str


# ---- 路由 -----------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.post("/api/chat")
async def chat(req: ChatRequest, deps: ChatDeps = Depends(get_chat_deps)) -> StreamingResponse:
    """以 SSE 流返回 Agent 事件（token / tool_call / tool_result / done）。"""

    async def event_stream():
        try:
            # Phase 3 默认 graph（LangGraph 编排）；loop 为 Phase 0-2 单轮工具循环（回归对比）。
            if settings.agent_mode == "graph":
                runner = run_research_graph(
                    req.message,
                    llm=deps.llm,
                    search=deps.search,
                    rag=deps.rag,
                    max_iterations=settings.agent_max_iterations,
                    memory=deps.memory,
                    memory_top_k=settings.memory_top_k,
                    checkpoint_db=settings.memory_checkpoint_db_path,
                )
            else:
                runner = run_research(
                    req.message, llm=deps.llm, search=deps.search, rag=deps.rag
                )
            async for event in runner:
                yield _sse_frame(event)
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

def _close_rag(rag: object | None) -> None:
    """任务结束清理临时知识库（task_{uuid} collection），幂等。"""
    close = getattr(rag, "close", None)
    if close is not None:
        close()


def _close_memory(memory: object | None) -> None:
    """任务结束关闭研究记忆存储（数据已落盘），幂等。"""
    close = getattr(memory, "close", None)
    if close is not None:
        close()


def _sse_frame(event: object) -> str:
    if isinstance(event, TokenEvent):
        payload = {"type": "token", "content": event.content}
    elif isinstance(event, ToolCallEvent):
        payload = {"type": "tool_call", "name": event.name, "arguments": event.arguments}
    elif isinstance(event, ToolResultEvent):
        payload = {"type": "tool_result", "summary": event.summary}
    elif isinstance(event, DoneEvent):
        payload = {"type": "done", "content": event.content}
    elif isinstance(event, PlanEvent):
        payload = {"type": "plan", "plan": event.plan}
    elif isinstance(event, StatusEvent):
        payload = {"type": "status", "message": event.message}
    elif isinstance(event, EvalEvent):
        payload = {
            "type": "eval",
            "sufficient": event.sufficient,
            "reason": event.reason,
            "iteration": event.iteration,
        }
    elif isinstance(event, MemoryEvent):
        payload = {"type": "memory", "found": event.found}
    else:
        raise TypeError(f"未知事件类型: {event!r}")
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
