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
    PlanEvent,
    StatusEvent,
    TokenEvent,
)
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.llm.client import LLMClient
from knowledge_pilot.llm.json_utils import parse_json_object
from knowledge_pilot.search.base import SearchProvider, SearchResult

# 研究-评估条件循环的兜底上限（runner 会用调用方传入值覆盖，此处仅作类型占位）。
DEFAULT_MAX_ITERATIONS = 3

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
    iteration: int
    max_iterations: int
    sufficient: bool
    refined_instruction: str
    report: str


# ---- 节点 ---------------------------------------------------------------


async def planner_node(state: ResearchState, *, llm: LLMClient, search: object = None, rag: object = None) -> dict:
    """拆解研究问题为子问题列表，发 PlanEvent。解析失败回退单步计划。"""
    writer = get_stream_writer()
    prompt = [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": state["query"]},
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
    state: ResearchState, *, llm: LLMClient, search: SearchProvider, rag: object | None
) -> dict:
    """跑 Agentic 工具循环收集证据；转发工具事件，丢弃过程 token 与内层 DoneEvent。

    每次迭代用 query + plan + 上一轮 evaluate 的 refined_instruction 组成研究指令。
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

    def collect(results: list[SearchResult]) -> None:
        """在工具边界采集结构化证据（默认 None 时行为不变）。"""
        for r in results[:3]:
            if r.url in seen:
                continue
            seen.add(r.url)
            evidence_new.append(
                EvidenceItem(source=r.url, title=r.title, snippet=r.content or r.snippet)
            )

    async for evt in run_research(
        focus,
        llm=llm,
        search=search,
        rag=rag,
        on_search_results=collect,
        system_prompt=RESEARCH_STEP_PROMPT,
    ):
        # 只转发工具事件：研究阶段的过程 token 与内层 DoneEvent 不应出现在最终流里。
        if isinstance(evt, (TokenEvent, DoneEvent)):
            continue
        writer(evt)

    return {"evidence": evidence_new}  # 只返回本轮新增（reducer 负责累计）


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
    """基于证据撰写带引用报告；报告作为 DoneEvent 结尾（与 run_research 语义一致）。"""
    writer = get_stream_writer()
    writer(StatusEvent(message="正在综合撰写报告…"))
    evidence_text = _format_evidence(state.get("evidence") or [])
    prompt = [
        {"role": "system", "content": SYNTHESIZE_PROMPT},
        {
            "role": "user",
            "content": (
                f"研究问题：{state['query']}\n"
                f"研究计划：{json.dumps(state.get('plan') or [], ensure_ascii=False)}\n"
                f"已收集资料（含来源）：\n{evidence_text or '（暂无）'}"
            ),
        },
    ]
    report = await llm.complete(prompt, max_tokens=4096)
    writer(DoneEvent(content=report))
    return {"report": report}


# ---- 路由 ---------------------------------------------------------------


def route_after_evaluate(state: ResearchState) -> Literal["research", "synthesize"]:
    """条件边：资料充分 或 已达迭代上限 → synthesize；否则回到 research 再研究一轮。"""
    if state.get("sufficient") or (state.get("iteration") or 0) >= state["max_iterations"]:
        return "synthesize"
    return "research"


# ---- 图构建与 runner ----------------------------------------------------


def _build_app(*, llm: LLMClient, search: SearchProvider, rag: object | None):
    """现建现编译（每次运行独立，天然并发隔离）。"""
    builder = StateGraph(ResearchState)
    builder.add_node("planner", partial(planner_node, llm=llm, search=search, rag=rag))
    builder.add_node("research", partial(research_node, llm=llm, search=search, rag=rag))
    builder.add_node("evaluate", partial(evaluate_node, llm=llm, search=search, rag=rag))
    builder.add_node("synthesize", partial(synthesize_node, llm=llm, search=search, rag=rag))
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "research")
    builder.add_edge("research", "evaluate")
    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"research": "research", "synthesize": "synthesize"},
    )
    builder.add_edge("synthesize", END)
    return builder.compile(checkpointer=MemorySaver())


async def run_research_graph(
    query: str,
    *,
    llm: LLMClient,
    search: SearchProvider,
    rag: object | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> AsyncIterator[object]:
    """驱动一次 LangGraph 研究任务，产出事件流（plan/status/tool/eval/done）。

    MemorySaver 编译后要求 thread_id；图每次现建现编译，thread_id 每次唯一。
    """
    app = _build_app(llm=llm, search=search, rag=rag)
    state: ResearchState = {
        "query": query,
        "plan": [],
        "evidence": [],
        "iteration": 0,
        "max_iterations": max_iterations,
        "sufficient": False,
        "refined_instruction": "",
        "report": "",
    }
    config = {"configurable": {"thread_id": f"research-{uuid4().hex}"}}

    saw_done = False
    async for part in app.astream(state, config=config, stream_mode="custom"):
        # 归一化 v1/v2 流协议：StreamPart 的负载在 .data。
        payload = getattr(part, "data", part)
        if isinstance(payload, DoneEvent):
            saw_done = True
        yield payload

    # 兜底：若 get_stream_writer 在个别 langgraph 版本未生效导致 DoneEvent 丢失，
    # 从 checkpoint 取最终 state 的报告补发（保证调用方总能收到 done）。
    if not saw_done:
        final = await app.aget_state(config)
        report = (final.values or {}).get("report") or ""
        if report:
            yield DoneEvent(content=report)


# ---- 工具函数 -----------------------------------------------------------


def _format_evidence(items: list[EvidenceItem]) -> str:
    lines = []
    for i, item in enumerate(items, start=1):
        snippet = item.snippet if len(item.snippet) <= 600 else item.snippet[:600] + "…"
        lines.append(f"[{i}] {item.title}\n    来源：{item.source}\n    {snippet}")
    return "\n\n".join(lines)
