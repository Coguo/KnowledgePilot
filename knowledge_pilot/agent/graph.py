"""LangGraph 编排：Planner → Research → Evaluate →（条件循环）→ Synthesis。

Phase 3 用 LangGraph 表达规格 §7 的研究流程：

    START → planner → research → evaluate → 资料是否充分？
        不充分 且 迭代未达上限 → research（条件循环）
        充分 或 已达上限      → synthesize → END

节点依赖（llm / search / rag）经 functools.partial 注入（图每次运行现建现编译，
天然并发隔离，对齐 create_rag_pipeline 约定）。节点内事件用 langgraph 的
get_stream_writer() 实时推到流（stream_mode="custom"），runner 直接把事件
yield 给调用方——事件流语义与 Phase 0-2 的 run_research 一致（DoneEvent 结尾）。

证据采集：research 节点复用现有 LLM tool-calling 循环（Agent 自主决定搜什么），
通过 run_research 的 on_search_results 钩子在工具边界拿到结构化 SearchResult。
"""

import json
import operator
from dataclasses import dataclass
from functools import partial
from typing import Annotated, AsyncIterator, Callable, Literal, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from knowledge_pilot.agent.events import (
    DoneEvent,
    EvalEvent,
    KgEvent,
    MemoryEvent,
    PlanEvent,
    StatusEvent,
    TokenEvent,
)
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.kg.extract import build_kg_context, extract_entities_relations
from knowledge_pilot.kg.graph import GraphStore, match_query_entities
from knowledge_pilot.llm.client import LLMClient
from knowledge_pilot.llm.json_utils import parse_json_object
from knowledge_pilot.memory.context import build_memory_context
from knowledge_pilot.memory.store import ResearchMemoryStore
from knowledge_pilot.search.base import SearchProvider, SearchResult

# 研究-评估条件循环的兜底上限（runner 会用调用方传入值覆盖，此处仅作类型占位）。
DEFAULT_MAX_ITERATIONS = 3

# 知识图谱抽取用的证据文本长度上限（防超长 token；超出截断）。
KG_EVIDENCE_MAX_CHARS = 8000

# 所有「输出 JSON」的 system prompt 必须包含单词 "json"：DeepSeek 的 json_object
# 模式硬性要求 prompt 出现该词，否则返回 HTTP 400。
PLANNER_PROMPT = (
    "你是研究规划助手。把用户的研究问题拆解为 2-4 个具体、可执行的研究子问题（步骤）。\n"
    '严格只输出 JSON（不要任何多余文字），格式为：\n'
    '{"steps": [{"title": "短标题", "question": "待研究的子问题", "purpose": "研究目的"}]}\n'
)

EVALUATE_PROMPT = (
    "你是研究质量评估员。判断已收集的资料是否足以撰写一份有依据的研究报告。\n"
    '严格只输出 JSON（不要任何多余文字），格式为：\n'
    '{"sufficient": true或false, "reason": "一句话理由", "gap": "不足时缺什么（充足时留空字符串）"}\n'
)

SYNTHESIZE_PROMPT = (
    "你是研究报告撰写员。基于研究问题、研究计划与已收集资料，撰写一份结构清晰、"
    "带来源引用的 markdown 研究报告。\n"
    "要求：\n"
    "1. 引用格式用 [1] [2] 标注来源，末尾列出来源列表（标题 + URL）。\n"
    "2. 结构建议：摘要 / 主体分节 / 结论 / 来源。\n"
    "3. 用中文。资料不足以回答的方面，明确说明。\n"
)

RESEARCH_STEP_PROMPT = (
    "你是研究执行助手。任务：围绕给定的研究问题与计划，通过 search_web 搜索并收集资料。\n"
    "规则：\n"
    "1. 每个子问题都应尝试搜索获取外部资料，再继续下一步。\n"
    "2. 资料足够后，简要总结本轮收集到的关键信息。\n"
    "3. 用中文。\n"
)


@dataclass
class EvidenceItem:
    """一条研究证据：来自某次搜索结果的来源与摘录。"""

    source: str
    title: str
    snippet: str


class ResearchState(TypedDict):
    query: str
    plan: list[dict]
    # reducer 必须：research 节点在循环中多次写入，整体替换会丢前几轮证据。
    evidence: Annotated[list[EvidenceItem], operator.add]
    # Phase 6：MCP 工具结果（截断后的文本块）跨轮累计，最后在 synthesize 渲染。
    # 与 evidence 分开：MCP 输出是自由文本补充资料，不进 EvidenceItem/来源列表/KG。
    notes: Annotated[list[str], operator.add]
    iteration: int
    max_iterations: int
    sufficient: bool
    refined_instruction: str
    kg_context: str  # Phase 5：构建出的「相关实体关系」prompt 块（空 = 未启用/无命中）
    report: str


# ---- 节点 ---------------------------------------------------------------


async def planner_node(
    state: ResearchState,
    *,
    llm: LLMClient,
    search: object = None,
    rag: object = None,
    memory_context: str | None = None,
) -> dict:
    """拆解研究问题为子问题列表，发 PlanEvent。解析失败回退单步计划。

    memory_context：Phase 4 召回的「历史研究背景」块（非空时拼进 user 消息头部，
    让规划参考用户已研究过的内容、深化或补缺，而不是重复研究）。
    """
    writer = get_stream_writer()
    user_content = state["query"]
    if memory_context:
        user_content = f"{memory_context}\n\n本次研究问题：{user_content}"
    prompt = [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": user_content},
    ]
    raw = await llm.complete(prompt, response_format={"type": "json_object"})
    parsed = parse_json_object(raw)
    steps = parsed.get("steps") if isinstance(parsed, dict) else None
    if not isinstance(steps, list) or not steps:
        steps = [
            {
                "title": "直接研究",
                "question": state["query"],
                "purpose": "解析失败，按原始问题单步研究",
            }
        ]
    writer(PlanEvent(plan=steps))
    return {"plan": steps}


async def research_node(
    state: ResearchState,
    *,
    llm: LLMClient,
    search: SearchProvider,
    rag: object | None = None,
    mcp: object | None = None,  # Phase 6 打开的 MCPGateway；None 时行为与 Phase 5 一致
) -> dict:
    """跑 Agentic 工具循环收集证据；转发工具事件，丢弃过程 token 与内层 DoneEvent。

    每次迭代用 query + plan + 上一轮 evaluate 的 refined_instruction 组成研究指令。

    mcp（Phase 6，默认 None 与 Phase 5 逐字节一致）：连接了工具时把 MCP 说明拼进
    研究 system prompt，并通过 on_extra_tool_result 钩子在工具边界把 MCP 输出
    落 notes（内层 LLM token 被丢弃，不落库则 MCP 结果到不了最终报告）。
    """
    writer = get_stream_writer()
    focus = state["query"]
    plan_text = "\n".join(f"- {s.get('question') or s.get('goal')}" for s in state["plan"])
    if plan_text:
        focus = f"{focus}\n\n研究计划：\n{plan_text}"
    if state.get("refined_instruction"):
        focus = f"{focus}\n\n补充要求：{state['refined_instruction']}"

    writer(StatusEvent(message="正在研究，收集资料…"))

    evidence_new: list[EvidenceItem] = []
    seen: set[str] = set()

    notes_new: list[str] = []
    seen_notes: set[str] = set()

    def collect(results: list[SearchResult]) -> None:
        """在工具边界采集结构化证据（默认 None 时行为不变）。"""
        for r in results[:3]:
            if r.url in seen:
                continue
            seen.add(r.url)
            evidence_new.append(
                EvidenceItem(source=r.url, title=r.title, snippet=r.content or r.snippet)
            )

    def collect_note(name: str, text: str) -> None:
        """MCP 工具结果落 notes：截断 + 本轮去重（供 synthesize 渲染补充资料）。"""
        note = f"[工具 {name}] {_truncate(text, 600)}"
        if note in seen_notes:
            return
        seen_notes.add(note)
        notes_new.append(note)

    # 研究导向提示：mcp 已连接工具时追加工具使用说明（教模型何时调用）。
    step_prompt = RESEARCH_STEP_PROMPT
    if mcp is not None and mcp.names():
        step_prompt = f"{RESEARCH_STEP_PROMPT}\n\n{mcp.prompt_hint()}"

    async for evt in run_research(
        focus,
        llm=llm,
        search=search,
        rag=rag,
        on_search_results=collect,
        system_prompt=step_prompt,
        mcp=mcp,
        on_extra_tool_result=collect_note,
    ):
        # 只转发工具事件：研究阶段的过程 token 与内层 DoneEvent 不应出现在最终流里。
        if isinstance(evt, (TokenEvent, DoneEvent)):
            continue
        writer(evt)

    # 只返回本轮新增（evidence/notes 的 reducer 负责跨轮累计）。
    return {"evidence": evidence_new, "notes": notes_new}


async def evaluate_node(state: ResearchState, *, llm: LLMClient, search: object = None, rag: object = None) -> dict:
    """LLM 判定资料是否充分；不足时给出 refined_instruction 供下一轮研究聚焦。

    解析失败默认视为「充分」（推进而非无限循环）。iteration 在此递增（唯一写者）。
    """
    writer = get_stream_writer()
    evidence_text = _format_evidence(state.get("evidence") or [])
    prompt = [
        {"role": "system", "content": EVALUATE_PROMPT},
        {
            "role": "user",
            "content": (
                f"研究问题：{state['query']}\n"
                f"已收集资料：\n{evidence_text or '（暂无）'}"
            ),
        },
    ]
    raw = await llm.complete(prompt, response_format={"type": "json_object"})
    parsed = parse_json_object(raw)
    if isinstance(parsed, dict):
        sufficient = bool(parsed.get("sufficient"))
        reason = str(parsed.get("reason") or "")
        gap = str(parsed.get("gap") or "")
    else:
        sufficient = True
        reason = "评估结果无法解析，按充分处理"
        gap = ""

    iteration = (state.get("iteration") or 0) + 1
    writer(EvalEvent(sufficient=sufficient, reason=reason, iteration=iteration))
    return {
        "sufficient": sufficient,
        "refined_instruction": gap,
        "iteration": iteration,
    }


async def synthesize_node(state: ResearchState, *, llm: LLMClient, search: object = None, rag: object = None) -> dict:
    """基于证据撰写带引用报告；报告作为 DoneEvent 结尾（与 run_research 语义一致）。

    kg_context（Phase 5，默认空）：非空时把「相关实体关系」块插在研究计划与
    已收集资料之间，让报告考虑结构化关系信息（与 RAG 的原始文本证据并存）。

    notes（Phase 6，默认空）：非空时在用户内容末尾追加「工具补充资料（MCP）」块。
    notes 按内容去重后渲染（跨研究轮可能重复）；为空则字符串与 Phase 5 逐字节一致。
    """
    writer = get_stream_writer()
    writer(StatusEvent(message="正在综合撰写报告…"))
    evidence_text = _format_evidence(state.get("evidence") or [])
    kg_context = state.get("kg_context") or ""
    user_content = (
        f"研究问题：{state['query']}\n"
        f"研究计划：{json.dumps(state.get('plan') or [], ensure_ascii=False)}\n"
    )
    if kg_context:
        user_content += f"{kg_context}\n\n"
    user_content += f"已收集资料（含来源）：\n{evidence_text or '（暂无）'}"

    notes = _unique_notes(state.get("notes") or [])
    if notes:
        user_content += (
            "\n\n工具补充资料（MCP，非网页搜索来源，仅供补充参考，不需要时可不引用）：\n"
            + "\n\n".join(notes)
        )
    prompt = [
        {"role": "system", "content": SYNTHESIZE_PROMPT},
        {"role": "user", "content": user_content},
    ]
    report = await llm.complete(prompt, max_tokens=4096)
    writer(DoneEvent(content=report))
    return {"report": report}


async def kg_node(state: ResearchState, *, llm: LLMClient, kg_hops: int = 2) -> dict:
    """Phase 5：从已收集证据抽取实体关系 → 建内存图 → 按查询匹配实体 → BFS 展开，
    把命中的三元组拼成「相关实体关系」块注入 synthesize。

    任何失败都不阻断研究：无实体/无命中 → kg_context=""，并发出 KgEvent 计数。
    """
    writer = get_stream_writer()
    writer(StatusEvent(message="正在构建知识图谱…"))

    text = _build_kg_extraction_text(state.get("evidence") or [])
    entities, relations = await extract_entities_relations(llm, text)
    if not entities:
        writer(KgEvent(entities=0, relations=0, found_triples=0))
        return {"kg_context": ""}

    store = GraphStore()
    store.add_entities(entities)
    store.add_relations(relations)
    matched = match_query_entities(store, state["query"])
    triples = store.query(matched, hops=kg_hops)

    writer(
        KgEvent(
            entities=store.node_count(),
            relations=store.edge_count(),
            found_triples=len(triples),
        )
    )
    return {"kg_context": build_kg_context(triples)}


# ---- 路由 ---------------------------------------------------------------


def route_after_evaluate(state: ResearchState) -> Literal["research", "synthesize"]:
    """条件边：资料充分 或 已达迭代上限 → synthesize；否则回到 research 再研究一轮。"""
    if state.get("sufficient") or (state.get("iteration") or 0) >= state["max_iterations"]:
        return "synthesize"
    return "research"


# ---- 图构建与 runner ----------------------------------------------------


def _build_app(
    *,
    llm: LLMClient,
    search: SearchProvider,
    rag: object | None,
    memory_context: str | None = None,
    checkpointer: object | None = None,
    kg_enabled: bool = False,
    kg_hops: int = 2,
    mcp: object | None = None,
):
    """现建现编译（每次运行独立，天然并发隔离）。

    checkpointer：None → MemorySaver（内存）；Phase 4 传入 SqliteSaver 时持久化到磁盘。
    kg_enabled（Phase 5）：在 evaluate（充分）与 synthesize 之间插入 kg 节点；
    禁用时图结构与 Phase 3/4 逐字节一致。mcp（Phase 6，默认 None）：research 节点
    透传 MCP 网关；None 时节点行为与 Phase 5 一致。
    """
    builder = StateGraph(ResearchState)
    builder.add_node(
        "planner",
        partial(planner_node, llm=llm, search=search, rag=rag, memory_context=memory_context),
    )
    builder.add_node(
        "research", partial(research_node, llm=llm, search=search, rag=rag, mcp=mcp)
    )
    builder.add_node("evaluate", partial(evaluate_node, llm=llm, search=search, rag=rag))
    builder.add_node("synthesize", partial(synthesize_node, llm=llm, search=search, rag=rag))
    if kg_enabled:
        builder.add_node("kg", partial(kg_node, llm=llm, kg_hops=kg_hops))
        builder.add_edge("kg", "synthesize")
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "research")
    builder.add_edge("research", "evaluate")
    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"research": "research", "synthesize": "kg" if kg_enabled else "synthesize"},
    )
    builder.add_edge("synthesize", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


async def _drive(app, state: ResearchState, config: dict, sink: dict) -> AsyncIterator[object]:
    """消费图事件流；结束后取最终 state 存入 sink（供落库与 DoneEvent 兜底复用）。

    兜底：若 get_stream_writer 在个别 langgraph 版本未生效导致 DoneEvent 丢失，
    从 checkpoint 取最终 state 的报告补发（保证调用方总能收到 done）。
    """
    saw_done = False
    async for part in app.astream(state, config=config, stream_mode="custom"):
        # 归一化 v1/v2 流协议：StreamPart 的负载在 .data。
        payload = getattr(part, "data", part)
        if isinstance(payload, DoneEvent):
            saw_done = True
        yield payload

    final = await app.aget_state(config)
    sink["values"] = final.values or {}
    if not saw_done and sink["values"].get("report"):
        yield DoneEvent(content=sink["values"]["report"])


def _dedup_sources(evidence: list) -> list[dict]:
    """evidence（EvidenceItem 对象/dict 列表）按 URL 去重为 {title, url} 来源列表。"""
    seen: set[str] = set()
    out: list[dict] = []
    for item in evidence:
        if isinstance(item, dict):
            url = item.get("source") or item.get("url") or ""
            title = item.get("title") or ""
        else:
            url = getattr(item, "source", "") or ""
            title = getattr(item, "title", "") or ""
        if url and url not in seen:
            seen.add(url)
            out.append({"title": title, "url": url})
    return out


async def run_research_graph(
    query: str,
    *,
    llm: LLMClient,
    search: SearchProvider,
    rag: object | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    memory: ResearchMemoryStore | None = None,
    memory_top_k: int = 3,
    checkpoint_db: str | None = None,
    kg_enabled: bool = False,
    kg_hops: int = 2,
    mcp: object | None = None,  # Phase 6 MCPGateway；None 时行为与 Phase 5 逐字节一致
) -> AsyncIterator[object]:
    """驱动一次 LangGraph 研究任务，产出事件流（plan/status/tool/eval/memory/kg/done）。

    memory（Phase 4，默认 None 行为与 Phase 3 逐字节一致）：非空时——
    开跑前召回相关历史注入 planner（并先发 MemoryEvent 提示），流结束后把本次
    研究落库；同时图 checkpoint 持久化到 checkpoint_db（SqliteSaver，懒导入失败
    回退 MemorySaver）。MemorySaver 编译后要求 thread_id；图每次现建现编译，
    thread_id 每次唯一。

    kg_enabled（Phase 5，默认 False 行为与 Phase 4 逐字节一致）：在 evaluate 与
    synthesize 之间插入 kg 节点——从证据抽实体关系建内存图、按查询匹配实体并
    BFS 展开，把三元组注入报告 prompt。只在 graph 模式生效（loop 模式无 KG）。

    mcp（Phase 6，默认 None 行为与 Phase 5 逐字节一致）：MCP 网关，research 节点
    用它把 server 工具并入研究循环，MCP 输出经 notes 累加器在 synthesize 渲染。
    """
    # 1) 记忆召回：新研究开始前，看用户以前研究过什么。
    memory_context = None
    if memory is not None:
        related = memory.search(query, top_k=memory_top_k)
        if related:
            memory_context = build_memory_context(related)
            yield MemoryEvent(found=len(related))

    state: ResearchState = {
        "query": query,
        "plan": [],
        "evidence": [],
        "notes": [],
        "iteration": 0,
        "max_iterations": max_iterations,
        "sufficient": False,
        "refined_instruction": "",
        "kg_context": "",
        "report": "",
    }
    config = {"configurable": {"thread_id": f"research-{uuid4().hex}"}}
    sink: dict = {}

    # 2) 编译图：memory 启用时用 SqliteSaver 持久化 checkpoint（懒导入，失败回退）。
    saver_cm = None
    if memory is not None and checkpoint_db:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            saver_cm = SqliteSaver.from_conn_string(checkpoint_db)
        except ImportError:
            saver_cm = None  # 未装 langgraph-checkpoint-sqlite → 回退 MemorySaver

    if saver_cm is not None:
        with saver_cm as saver:
            app = _build_app(
                llm=llm,
                search=search,
                rag=rag,
                memory_context=memory_context,
                checkpointer=saver,
                kg_enabled=kg_enabled,
                kg_hops=kg_hops,
                mcp=mcp,
            )
            async for payload in _drive(app, state, config, sink):
                yield payload
    else:
        app = _build_app(
            llm=llm,
            search=search,
            rag=rag,
            memory_context=memory_context,
            checkpointer=None,
            kg_enabled=kg_enabled,
            kg_hops=kg_hops,
            mcp=mcp,
        )
        async for payload in _drive(app, state, config, sink):
            yield payload

    # 3) 落库：研究完成后持久化 query / plan / evidence / report / sources。
    if memory is not None:
        values = sink.get("values") or {}
        evidence = values.get("evidence") or []
        memory.save_run(
            query,
            plan=values.get("plan") or [],
            evidence=evidence,
            report=values.get("report") or "",
            sources=_dedup_sources(evidence),
        )


# ---- 工具函数 -----------------------------------------------------------


def _format_evidence(items: list[EvidenceItem]) -> str:
    lines = []
    for i, item in enumerate(items, start=1):
        snippet = item.snippet if len(item.snippet) <= 600 else item.snippet[:600] + "…"
        lines.append(f"[{i}] {item.title}\n    来源：{item.source}\n    {snippet}")
    return "\n\n".join(lines)


def _truncate(text: str, max_len: int) -> str:
    """截断到 max_len 字符并加省略号（notes/工具结果进 prompt 前的尺寸控制）。"""
    text = text or ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _unique_notes(notes: list[str]) -> list[str]:
    """notes 保序去重（跨研究轮可能产生重复的 MCP 结果块）。"""
    seen: set[str] = set()
    out: list[str] = []
    for note in notes:
        if note in seen:
            continue
        seen.add(note)
        out.append(note)
    return out


def _build_kg_extraction_text(evidence: list) -> str:
    """把证据拼成知识图谱抽取输入：每条带来源标记；超长截断到 KG_EVIDENCE_MAX_CHARS。"""
    lines = []
    for item in evidence:
        if isinstance(item, dict):
            source = item.get("source") or ""
            title = item.get("title") or ""
            snippet = item.get("snippet") or ""
        else:
            source = getattr(item, "source", "") or ""
            title = getattr(item, "title", "") or ""
            snippet = getattr(item, "snippet", "") or ""
        lines.append(f"[来源：{source} | {title}]\n{snippet}")
    text = "\n\n".join(lines)
    if len(text) > KG_EVIDENCE_MAX_CHARS:
        return text[:KG_EVIDENCE_MAX_CHARS] + "…"
    return text
