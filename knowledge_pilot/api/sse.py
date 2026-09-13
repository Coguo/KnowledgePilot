"""SSE 帧编码：Agent 事件 → `data: {...}\\n\\n`。

`/api/chat` 与 `/api/learning/*` 共用同一套协议，所以编码器独立于任何 router 存在。
"""

import json
import re

from knowledge_pilot.agent.events import (
    DoneEvent,
    ErrorEvent,
    EvalEvent,
    GraphReadyEvent,
    KgEvent,
    MemoryEvent,
    NodesEvent,
    PlanEvent,
    RecommendEvent,
    StatusEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)

# 密钥形态兜底过滤：openai SDK 的部分异常文本会带上请求上下文（含 Authorization
# 头），原样回给浏览器等于把 key 打进页面正文与浏览器历史。
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")


def safe_error_text(exc: BaseException) -> str:
    """异常 → 可展示给前端的一行文本：脱敏 + 压平空白 + 截断。"""
    raw = f"{type(exc).__name__}: {exc}"
    raw = _SECRET_RE.sub("sk-***", raw)
    raw = " ".join(raw.split())
    return raw[:300] + "…" if len(raw) > 300 else raw


def sse_frame(event: object) -> str:
    if isinstance(event, TokenEvent):
        payload = {"type": "token", "content": event.content}
    elif isinstance(event, ToolCallEvent):
        payload = {"type": "tool_call", "name": event.name, "arguments": event.arguments}
    elif isinstance(event, ToolResultEvent):
        payload = {"type": "tool_result", "summary": event.summary}
    elif isinstance(event, DoneEvent):
        payload = {"type": "done", "content": event.content}
    elif isinstance(event, ErrorEvent):
        payload = {"type": "error", "message": event.message}
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
    elif isinstance(event, KgEvent):
        payload = {
            "type": "kg",
            "entities": event.entities,
            "relations": event.relations,
            "found_triples": event.found_triples,
        }
    elif isinstance(event, NodesEvent):
        # **只发计数，不发节点本身**（与 `GraphReadyEvent` 同一个理由）：前端拿到
        # 只是为了在日志里说一句「抽出了 N 个知识点」，真正要渲染的那张图走
        # `GET /api/learning/topics/{id}` 拉——两处数据源一定会分叉。
        payload = {"type": "nodes", "count": len(event.nodes or []), "summary": event.summary}
    elif isinstance(event, GraphReadyEvent):
        payload = {
            "type": "graph_ready",
            "topic_id": event.topic_id,
            "nodes": event.nodes,
            "edges": event.edges,
            "degraded": event.degraded,
        }
    elif isinstance(event, RecommendEvent):
        payload = {
            "type": "recommend",
            "node_id": event.node_id,
            "reason": event.reason,
            "confidence": event.confidence,
        }
    else:
        raise TypeError(f"未知事件类型: {event!r}")
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
