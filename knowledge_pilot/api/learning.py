"""学习图谱 API（`/api/learning/*`）。

**默认关**：`LEARNING_ENABLED=false`（默认）时全部路由 503——既不建库也不建目录，
Phase 0–8 的行为逐字节不变。

读写分离是这个模块的核心纪律：

- **写**（create / generate / chat / mastery）才可能触发 LLM；generate 与 chat 是 SSE；
- **读**（topics 列表 / 整图 / 知识点正文 / 对话历史）**一律零 LLM、零检索**，
  也不需要 API key —— 「重启后直接读回、不重新检索」这条需求，落在这里就是
  「GET 路由不碰任何会花钱的依赖」。所以 GET 路由**不** `Depends(get_chat_deps)`。
"""

from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from knowledge_pilot.agent.events import (
    DoneEvent,
    ErrorEvent,
    GraphReadyEvent,
    NodesEvent,
    PlanEvent,
    StatusEvent,
    TokenEvent,
)
from knowledge_pilot.agent.graph import run_research_graph
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.api.deps import (
    ChatDeps,
    acquire_mcp_gateway,
    close_memory,
    close_rag,
    get_chat_deps,
)
from knowledge_pilot.api.sse import safe_error_text, sse_frame
from knowledge_pilot.config import clamp_rounds, settings
from knowledge_pilot.learning import create_learning_store
from knowledge_pilot.learning import notes as notes_mod
from knowledge_pilot.learning import service, session
from knowledge_pilot.learning.path import LEARNING_PATH_PROMPT

router = APIRouter(prefix="/api/learning", tags=["learning"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 避免 nginx 缓冲影响流式
}


def require_learning() -> None:
    """开关闸门：未启用时 503（而不是 404——404 会让人以为路由写错了）。"""
    if not settings.learning_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "学习图谱未启用：请在 .env 中设置 LEARNING_ENABLED=true 后重启服务。"
            ),
        )


@contextmanager
def _store():
    """每次请求现开现关（对齐 `open_read_store` 的 Windows 句柄结论）。"""
    store = create_learning_store(settings.learning_db_path)
    try:
        yield store
    finally:
        store.close()


# ---- 请求模型 ---------------------------------------------------------


class TopicCreateRequest(BaseModel):
    query: str
    title: str = ""


class ChatRequest(BaseModel):
    message: str


class MasteryRequest(BaseModel):
    mastered: bool = True


class OutlineRequest(BaseModel):
    """「重新生成提纲」用。默认 `force=False` = 老口径的按需生成（已有就回读）。"""

    force: bool = False


class NoteRequest(BaseModel):
    """保存「我的笔记」用。

    `max_length` 不是防恶意（本地单用户应用）：它是**挡住一个真实的失手**——把整份
    讲解记录或者别的文件粘进文本框再保存，一次就写进用户的知识点文件里。100k 字符
    远大于任何人手写的笔记，也不会因为一份长得离谱的记录被误伤。
    """

    note: str = Field("", max_length=100_000)


# ---- 主题 -------------------------------------------------------------


@router.get("/topics")
def list_topics(_: None = Depends(require_learning)) -> dict:
    """主题列表 + 进度统计。纯读。"""
    with _store() as store:
        return {"topics": store.list_topics()}


@router.post("/topics")
def create_topic(
    req: TopicCreateRequest, _: None = Depends(require_learning)
) -> dict:
    """建主题壳（不调 LLM、不建正文文件）——立刻返回一个可点的条目。

    与「生成」分开的理由：生成要几十秒，建壳必须秒回；生成失败也还有个壳能重试。
    """
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    with _store() as store:
        return service.create_topic_shell(store, query, title=req.title)


@router.get("/topics/{topic_id}")
def get_topic(topic_id: str, _: None = Depends(require_learning)) -> dict:
    """整图：nodes（含 depth / order_index / status）+ edges + 推荐顺序。纯读。"""
    with _store() as store:
        graph = store.get_graph(topic_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="主题不存在")
    return graph


@router.delete("/topics/{topic_id}")
def delete_topic(topic_id: str, _: None = Depends(require_learning)) -> dict:
    """删主题（连同图 / 状态 / 对话）。**默认保留 Markdown 正文**——那是学习资产。"""
    with _store() as store:
        if store.get_topic(topic_id) is None:
            raise HTTPException(status_code=404, detail="主题不存在")
        kept = store.delete_topic(topic_id)
    return {"deleted": topic_id, "notes_kept": kept}


# ---- 生成（SSE） ------------------------------------------------------


@router.post("/topics/{topic_id}/generate")
async def generate_topic(
    topic_id: str,
    _: None = Depends(require_learning),
    deps: ChatDeps = Depends(get_chat_deps),
) -> StreamingResponse:
    """研究该主题 → **直接抽知识点** → 落库 + 生成 Markdown → `graph_ready`。

    复用 `/api/chat` 的同一条研究管线（模型、RAG、Memory、MCP 全部沿用），差别只在
    收尾：graph 模式下研究图的收尾节点是 `extract`（`extract_prompt=LEARNING_PATH_PROMPT`），
    它不写报告，直接把知识点当 `NodesEvent` 交出来；loop 模式没有这个节点，仍走
    「报告 → 抽节点」那条老路（见 `_generate_stream` 的分支）。
    """
    with _store() as store:
        topic = store.get_topic(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="主题不存在")

    return StreamingResponse(
        _generate_stream(topic_id, topic["query"], deps),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


async def _generate_stream(topic_id: str, query: str, deps: ChatDeps):
    """生成流。异常一律转成 `error` 帧并把主题落为 `failed`（可重试），不留假图。

    第六轮起 graph 模式**不再写研究报告**，收尾节点直接把知识点交回来（`NodesEvent`）。
    报告只在与 `/api/chat` 共用的那条路上还有意义（loop 模式）。

    这里有两件必须一起看的事：

    - **留白**：图谱模式下 `#gen-body` 收到的是**渲染好的大纲**（零 LLM 的纯函数产物，
      在最后一次性发出）。它不是「逐字报告」，也不该是——用户要的是图谱与每个节点的
      关键词，而大纲正好是这两样的文字形式，还能顺手进 `report.md` 与记忆；
    - **闸门**：图建不出来（既没抽到知识点、也没有研究计划可降级）就 `raise`，
      由 `except` 落 `failed` + `error` 帧。**`persist_graph` 一次都不会被调用**，
      所以上一次的好图原样留在库里——而不是被一张只有一个点的图覆盖掉，
      那正是用户报的那个故障。
    """
    store = create_learning_store(settings.learning_db_path)
    report = ""
    nodes: list[dict] = []
    plan: list = []
    summary = ""
    try:
        store.update_topic(topic_id, status="generating", error="")

        gateway = await acquire_mcp_gateway()
        cap = settings.agent_rounds_hard_cap
        common = {
            "llm": deps.llm,
            "search": deps.search,
            "rag": deps.rag,
            "max_tool_rounds": clamp_rounds(
                None, settings.agent_max_tool_rounds, cap
            ),
        }
        graph_mode = settings.agent_mode == "graph"
        if graph_mode:
            runner = run_research_graph(
                query,
                **common,
                max_iterations=clamp_rounds(
                    None, settings.agent_max_iterations, cap
                ),
                memory=deps.memory,
                memory_top_k=settings.memory_top_k,
                checkpoint_db=settings.memory_checkpoint_db_path,
                kg_enabled=settings.kg_enabled,
                kg_hops=settings.kg_hops,
                mcp=gateway,
                # 收尾换成「抽知识点」：见 `_generate_stream` 的 docstring。
                extract_prompt=LEARNING_PATH_PROMPT,
            )
        else:
            runner = run_research(query, **common)

        async for event in runner:
            # 三条收尾信息各走各的字段，谁也不兼任：`plan` 是降级时的备料，
            # `NodesEvent` 是图谱模式下的主产物，`DoneEvent.content` 是报告
            # （loop 模式；图谱模式下它是那句一句话结论，仅作正文用）。
            if isinstance(event, PlanEvent):
                plan = event.plan or []
            elif isinstance(event, NodesEvent):
                nodes, summary = list(event.nodes or []), event.summary or ""
            elif isinstance(event, DoneEvent):
                report = event.content
            yield sse_frame(event)

        if graph_mode:
            graph, degraded = service.build_graph_from_material(
                nodes, plan, max_nodes=settings.learning_max_nodes
            )
            # 大纲是**渲染**出来的（纯函数），不是模型写的——所以「文件里有什么」
            # 不再取决于那一次调用有没有被推理吃光预算。
            outline = notes_mod.render_outline(graph["nodes"]) if graph["nodes"] else ""
        else:
            graph, degraded = await service.build_graph_from_report(
                deps.llm,
                report,
                max_nodes=settings.learning_max_nodes,
                topic_title=store.get_topic(topic_id)["title"],
            )
            outline = report

        if not graph["nodes"]:
            # 闸门：宁可让用户看到一次「生成失败 + 重试」，也不要交一张只有一个点的图。
            raise RuntimeError(degraded or service.NOTHING_TO_BUILD)
        if degraded:
            yield sse_frame(StatusEvent(message=degraded))

        stats = service.persist_graph(
            store,
            topic_id,
            graph,
            notes_dir=settings.learning_notes_dir,
            report=outline,
        )
        store.update_topic(
            topic_id, status="ready", summary=summary or _lead(outline)
        )
        if graph_mode and outline:
            # 一次性把大纲发给前端（`#gen-body`）。**在图与文件都落好之后**：
            # 用户看到它时，磁盘上已经有了同一个文件。
            yield sse_frame(TokenEvent(content=outline))
        yield sse_frame(
            GraphReadyEvent(
                topic_id=topic_id,
                nodes=stats["nodes"],
                edges=stats["edges"],
                degraded=bool(degraded),
            )
        )
    except Exception as exc:  # noqa: BLE001 — 流内异常必须变成一帧，并让主题可重试
        store.update_topic(topic_id, status="failed", error=safe_error_text(exc))
        yield sse_frame(ErrorEvent(message=safe_error_text(exc)))
    finally:
        store.close()
        close_rag(deps.rag)
        close_memory(deps.memory)
        yield "data: [DONE]\n\n"


def _lead(report: str, *, limit: int = 100) -> str:
    """报告开头第一句**正文**，给主题列表当副标题。

    优先取非标题行：报告的第一行几乎总是 `## 文本切分`（第一个知识点的名字），拿它当
    整个主题的摘要等于没说。真的一句正文都没有时才退回第一个标题。
    """
    headings: list[str] = []
    for raw in (report or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            headings.append(line.lstrip("#").strip())
            continue
        return line[:limit]
    for heading in headings:
        if heading:
            return heading[:limit]
    return ""


# ---- 知识点 -----------------------------------------------------------


@router.get("/nodes/{node_id}")
def get_node(node_id: str, _: None = Depends(require_learning)) -> dict:
    """知识点 + Markdown 正文。**纯读，零 LLM**（打开节点不会自动开讲）。"""
    with _store() as store:
        node = service.load_node_payload(store, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    return node


@router.get("/nodes/{node_id}/messages")
def get_messages(node_id: str, _: None = Depends(require_learning)) -> dict:
    """该知识点的历史对话。**纯读**——刷新页面靠它恢复，不重新问模型。"""
    with _store() as store:
        if store.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail="知识点不存在")
        return {"messages": store.list_messages(node_id)}


@router.post("/nodes/{node_id}/outline")
async def ensure_outline(
    node_id: str,
    req: OutlineRequest | None = None,
    _: None = Depends(require_learning),
    deps: ChatDeps = Depends(get_chat_deps),
) -> dict:
    """打开某个知识点时**按需**生成它的提纲（`## 提纲`），返回最新的正文。

    **为什么是 POST 而不是 GET**：它可能调一次 LLM。这条纪律来自本模块开头的读写
    分离——GET 一律零 LLM、不需要 API key，所以「刷新页面只发 GET 就能恢复全部状态」
    才成立。放进 GET 会让每一次刷新都重新生成一遍提纲。

    **为什么前端在 `body` 里没看到 `## 提纲` 才调它**：判断成本为零（正文本来就在
    手里），代价是「同一份正文在前后端各判一次」——两边的判据都只是「有没有这一段
    整行标题」，且后端这一层是**幂等**的（已有就回读，不再花钱），所以判错也自愈。

    失败不是错误：模型挂了 / 取不出条目时返回 `{"outline": [], "generated": false}`，
    前端显示一个可重试的提示。这是有意的——提纲会被写进用户自己的文件，所以宁可
    什么都不写，也不留一份半成品（见 `learning/outline.py`）。

    `{"force": true}` = 「重新生成」：跳过「已有就回读」那道闸门，用
    `notes.replace_outline` 把 `## 提纲` 那一段换掉（走查反馈 ② 改了提纲的颗粒度，
    旧节点文件里躺着的是老口径那一版）。它**消耗一次 LLM 调用**，所以只有用户明确
    点那个按钮时才会发。request body 因此是可选的：不带 body = 老行为。
    """
    try:
        with _store() as store:
            return await service.ensure_node_outline(
                store,
                node_id,
                llm=deps.llm,
                notes_dir=settings.learning_notes_dir,
                max_items=settings.learning_outline_max_items,
                force=bool(req and req.force),
            )
    except ValueError:
        raise HTTPException(status_code=404, detail="知识点不存在") from None
    finally:
        close_rag(deps.rag)
        close_memory(deps.memory)


@router.post("/nodes/{node_id}/chat")
async def chat_with_node(
    node_id: str,
    req: ChatRequest,
    _: None = Depends(require_learning),
    deps: ChatDeps = Depends(get_chat_deps),
) -> StreamingResponse:
    """知识点对话（SSE）：讲解逐字流出 →（够格时）`recommend` → `done`。

    判定只在「未学 + 聊够轮数 + 开关打开」时发生（见 `learning/session.py` 的三道闸门），
    所以正常追问不会每次都多花一次判定调用。
    """
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    with _store() as store:
        if store.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail="知识点不存在")

    async def event_stream():
        store = create_learning_store(settings.learning_db_path)
        try:
            async for event in session.run_node_chat(
                store,
                node_id,
                message,
                llm=deps.llm,
                recommend_enabled=settings.learning_recommend_enabled,
                min_turns=settings.learning_recommend_min_turns,
                confidence_threshold=settings.learning_mastery_confidence,
            ):
                yield sse_frame(event)
        except Exception as exc:  # noqa: BLE001 — 流内异常必须变成一帧发给前端
            yield sse_frame(ErrorEvent(message=safe_error_text(exc)))
        finally:
            store.close()
            close_rag(deps.rag)
            close_memory(deps.memory)
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.post("/nodes/{node_id}/mastery")
def set_mastery(
    node_id: str, req: MasteryRequest, _: None = Depends(require_learning)
) -> dict:
    """用户确认点亮 / 撤销点亮。纯状态转移，零 LLM。

    不自动降级、不自动点亮：系统只会**推荐**，置为 `mastered` 只能由这个端点（用户的
    显式动作）触发。Markdown 里的 `status` 同步失败不影响点亮本身——DB 才是权威。
    """
    with _store() as store:
        node = store.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="知识点不存在")
        try:
            status = store.set_node_status(
                node_id, "mastered" if req.mastered else "unlearned"
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if node.get("note_path"):
            notes_mod.update_note_status(node["note_path"], status)
        return {"node_id": node_id, "status": status}


@router.post("/nodes/{node_id}/note")
def save_note(node_id: str, req: NoteRequest, _: None = Depends(require_learning)) -> dict:
    """保存右侧「我的笔记」那一段，返回**改完之后**的完整正文。

    **为什么它必须是 POST**：与 `/mastery` 一样是纯写，但它改了用户磁盘上的文件，
    而 GET 的语义是幂等可重放的（浏览器/代理也这么假设）。

    **为什么它不依赖 `ChatDeps`**：零 LLM。这一条和 `/outline` 正相反 —— 那边要花钱，
    所以带了 `deps`；这边不该因为「没配 API key」就存不了笔记。本模块开头的读写分离
    纪律在这里的落点是：**写口一律不带 `deps`**。

    **只动 `## 我的笔记` 那一段**（`notes.write_user_notes`）：讲解记录是只追加的学习
    资产、程序还要往里写，整份可编辑会让两边互相覆盖（用户已拍板只改这一段）。

    返回 `body` 而不是 204：前端要把新正文放回 `S.node.body` —— 不返回的话，界面上的
    正文与磁盘上的会在下一次保存前一直是两份，而用户看不出哪份是真的。

    文件被外部删掉时**先 `rehome_note` 重建骨架再写**（同 `ensure_node_outline` 的
    第三道闸门）：少了这一步，`write_user_notes` 返回 False，而用户的现象是「保存了，
    但文件里没有」——没有任何报错。
    """
    with _store() as store:
        if store.get_node(node_id) is None:
            raise HTTPException(status_code=404, detail="知识点不存在")
        path = service.note_file(store, node_id, notes_dir=settings.learning_notes_dir)
        saved = notes_mod.write_user_notes(path, req.note)
    return {"node_id": node_id, "saved": saved, "body": notes_mod.read_text(path) or ""}
