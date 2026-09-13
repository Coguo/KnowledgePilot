"""请求依赖装配：LLM / 搜索 / RAG / Memory。

独立成模块是为了让 `/api/chat`（研究入口）与 `/api/learning/*`（学习入口）共用同一套
装配逻辑，而不必让其中一个去 import 另一个（那会绕成循环 import）。

`main.py` 仍然 re-export 这里的名字（`ChatDeps` / `get_chat_deps`）——依赖覆盖是
按**函数对象**做键的（`app.dependency_overrides[get_chat_deps]`），只要两处拿到的是
同一个对象，测试的覆盖就照常生效。
"""

from dataclasses import dataclass

from fastapi import HTTPException

from knowledge_pilot.config import settings
from knowledge_pilot.llm.client import LLMClient
from knowledge_pilot.llm.gateway import build_llm_client
from knowledge_pilot.memory import create_memory_store
from knowledge_pilot.search import SearchProvider, create_search_provider


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
    # Phase 8：经 Model Gateway 装配（默认单 DeepSeek provider、一次调用，
    # 超时/重试用 SDK 默认 → 与旧 ChatClient 逐字节一致；重试/fallback 全 opt-in）。
    llm = build_llm_client(settings)

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

    # Phase 6 MCP：stdio 子进程是 async 的，网关装不进 sync 依赖（事件流里按请求
    # 开/关）。这里只做同步可用性预检（mcp 未装 → 清晰 500，避免流里崩）。
    if settings.mcp_enabled and settings.agent_mode == "graph":
        try:
            import mcp  # noqa: F401 — 探测官方 mcp SDK（base 依赖）
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "MCP 已启用但缺少依赖 mcp：请先运行 "
                    "pip install -e \".[dev,rag]\" 后重启服务。"
                ),
            ) from exc

    return ChatDeps(
        llm=llm,
        search=search,
        rag=rag,
        memory=memory,
    )


async def acquire_mcp_gateway():
    """取 Phase 8 的**常驻** MCP 网关；默认关 / 连不上 → None。

    子进程跨请求复用（消掉每请求 0.3–0.5s spawn）。经进程级 MCPRuntime 懒连接 +
    只对断开的 spec 冷却补连；**不在这里关闭**——lifespan 负责整体关闭。
    失败一律吞掉：MCP 是可选增强，连不上就该退回无 MCP 的研究路径，而不是让整轮失败。
    """
    if not (settings.mcp_enabled and settings.agent_mode == "graph"):
        return None
    try:
        from knowledge_pilot.mcp.gateway import build_mcp_specs
        from knowledge_pilot.mcp.runtime import get_mcp_runtime

        runtime = get_mcp_runtime()
        gw = await runtime.ensure(build_mcp_specs(settings.memory_db_path))
        if gw is not None:
            await runtime.refresh()
        return gw
    except Exception:  # noqa: BLE001 — 常驻失败退回无 MCP 路径
        return None


def close_rag(rag: object | None) -> None:
    """任务结束清理临时知识库（task_{uuid} collection），幂等。"""
    close = getattr(rag, "close", None)
    if close is not None:
        close()


def close_memory(memory: object | None) -> None:
    """任务结束关闭研究记忆存储（数据已落盘），幂等。"""
    close = getattr(memory, "close", None)
    if close is not None:
        close()
