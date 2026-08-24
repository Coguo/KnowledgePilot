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
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.config import settings
from knowledge_pilot.llm.client import ChatClient, LLMClient
from knowledge_pilot.search import SearchProvider, create_search_provider

app = FastAPI(title="KnowledgePilot — AI Research Agent", version="0.1.0")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# ---- 依赖注入 -------------------------------------------------------

@dataclass
class ChatDeps:
    llm: LLMClient
    search: SearchProvider


def get_chat_deps() -> ChatDeps:
    """组装本次请求的 LLM 与搜索依赖。未配置密钥时给出清晰错误。"""
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
    return ChatDeps(
        llm=ChatClient(settings),
        search=search,
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
            async for event in run_research(req.message, llm=deps.llm, search=deps.search):
                yield _sse_frame(event)
        finally:
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

def _sse_frame(event: object) -> str:
    if isinstance(event, TokenEvent):
        payload = {"type": "token", "content": event.content}
    elif isinstance(event, ToolCallEvent):
        payload = {"type": "tool_call", "name": event.name, "arguments": event.arguments}
    elif isinstance(event, ToolResultEvent):
        payload = {"type": "tool_result", "summary": event.summary}
    elif isinstance(event, DoneEvent):
        payload = {"type": "done", "content": event.content}
    else:
        raise TypeError(f"未知事件类型: {event!r}")
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
