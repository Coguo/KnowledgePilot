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
    NodesEvent,
    PlanEvent,
    StatusEvent,
    TokenEvent,
)
from knowledge_pilot.agent.loop import run_research
from knowledge_pilot.kg.extract import build_kg_context, extract_entities_relations
from knowledge_pilot.kg.graph import GraphStore, match_query_entities
from knowledge_pilot.llm.client import LLMClient
from knowledge_pilot.llm.json_utils import parse_json_object, salvage_json
from knowledge_pilot.llm.providers import THINKING_OFF
from knowledge_pilot.llm.streaming import stream_text
from knowledge_pilot.memory.context import build_memory_context
from knowledge_pilot.memory.store import ResearchMemoryStore
from knowledge_pilot.search.base import SearchProvider, SearchResult

# 研究-评估条件循环的兜底上限（runner 会用调用方传入值覆盖，此处仅作类型占位）。
DEFAULT_MAX_ITERATIONS = 3

# 知识图谱抽取用的证据文本长度上限（防超长 token；超出截断）。
KG_EVIDENCE_MAX_CHARS = 8000

# 学习侧「抽取知识点」这一步的输出预算。**给得比 synthesize 的 4096 宽**：同一个
# 推理模型（deepseek-flash 之类）的 reasoning_content 与正文**共用**这个预算，而
# 预算被推理吃光时正文会是**空字符串且不报错**——4096 下实测推理就能吃掉 3000+。
#
# 第七轮 8192 → 16384，同时给这一步关掉思考（见 `_EXTRACT_EXTRA_BODY`）——**关思考
# 才是修复，加宽只是保险**。上一版把「加宽」当成修复，方向是错的：8192 实测推理吃掉
# 19474 字、正文只剩 1418 字且断在半句（`finish_reason=length`），因为**危险程度与
# 「想了多久」成正比，而不是与「读了多少资料」成正比** —— 同一份 9275 字的 prompt
# 有时想 19474 字、有时想 20369 字（那次正文 0 字），所以没法按输入长度估预算。
# 关掉思考后同一次抽取只花 1293 个输出 token 就交回 12 个节点。留宽到 16384 是因为
# **额度只卡输出、不预留**（多给的不会挤占输入），万一方言被某个 provider 拒了，
# 这里还有余量撑住推理。
EXTRACT_MAX_TOKENS = 16384

# 「关掉思考」这个 provider 方言（第七轮）。它由 `llm/providers.py` 定义——agent 层
# 只把它当**不透明字典**原样递下去，不需要认识任何一个具体方言。
_EXTRACT_EXTRA_BODY = THINKING_OFF

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
    # 学习侧（extract 收尾）：从资料抽出的知识点**原始** dict 列表（未清洗、未建图——
    # 清洗与建图是 `learning/path.py` 的事，agent 层不认识「知识点」这个词）。
    # 只在 extract 收尾时写入；`synthesize` 收尾的路径里它一直缺席，所以读它一律用
    # `state.get("nodes")`——旧 checkpoint 里没有这个键。
    nodes: list[dict]


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
    max_tool_rounds: int | None = None,  # Phase 9 透传给 run_research；None → 用其常量
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
        max_tool_rounds=max_tool_rounds,
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

    Phase 9：报告经 `_report_text` 获取——LLM 实现了 `stream_complete` 时先逐字推
    TokenEvent 再补 DoneEvent（长报告不再让用户干等），否则退回 `complete`（既有测试
    路径逐字节不变）。两种路径的最终 `DoneEvent.content` 一定等于 token 拼接。
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
    report = await _report_text(llm, prompt, writer, max_tokens=4096)
    writer(DoneEvent(content=report))
    return {"report": report}


async def extract_node(
    state: ResearchState,
    *,
    llm: LLMClient,
    system_prompt: str,
    max_tokens: int = EXTRACT_MAX_TOKENS,
) -> dict:
    """学习侧收尾节点：**直接从资料抽出知识点**，不写报告。

    它替代的是一条真实存在的故障链：`synthesize` 写长报告（4096 预算，推理模型会
    把预算吃光）→ 正文空 → 学习侧拿到空报告 → 降级成「一个节点」。报告本身又只是
    中间产物（学的是节点，不是报告），所以这里是**把中间产物删掉**，而不是把它的
    预算调大。

    输入复用 `synthesize` 那一份（研究问题 + 研究计划 + 已收集资料），只是最后一步
    从「写文章」换成「输出 JSON」：JSON 短得多。

    **第六轮那句「失败模式从『一个字都没有』变成『抽得差』」是错的**（第七轮实测纠正）：
    同样的预算下它照样会一个字都没有（一次真实抽取 reasoning 20369 字 / 正文 0 字），
    或者更常见地**断在半句**（reasoning 19474 / 正文 1418 字，`finish_reason=length`）。
    真正把这一步救回来的是**关掉思考**（`_EXTRACT_EXTRA_BODY`）——推理与正文共用
    `max_tokens`，而抽取这一步不需要推理；关掉之后同一次抽取只花 1293 个输出 token。
    `salvage_json` 只兜住「有正文但被截断」这一种，正文为空时它无从下手。

    与 synthesize 的两处刻意不同：
    - **不流式**。JSON 逐字吐给用户没有意义（他要点的是那张图），所以走 `complete`;
    - **异常不抛**。抽取失败 = 交不出节点 = 由调用方降级（研究计划 → 线性路径），
      与研究图别处的「永不阻断」取舍一致。`llm.complete` 抛出的错误在这里被吃掉，
      但它同时会让 planner/evaluate 也失败 → 计划为空 → 由调用方落 failed（那才是
      该报错的场合，且报得出来）。

    `system_prompt` 由调用方注入（学习层给 `LEARNING_PATH_PROMPT`）：agent 层不认识
    「知识点」，它只负责「按这个 prompt 要一份 {nodes, summary} 的 JSON」。
    """
    writer = get_stream_writer()
    writer(StatusEvent(message="正在从资料中抽取知识点…"))
    user_content = (
        f"研究问题：{state['query']}\n"
        f"研究计划：{json.dumps(state.get('plan') or [], ensure_ascii=False)}\n"
        f"已收集资料（含来源）：\n"
        f"{_build_kg_extraction_text(state.get('evidence') or []) or '（暂无）'}"
    )
    prompt = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        raw = await llm.complete(
            prompt,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            extra_body=_EXTRACT_EXTRA_BODY,
        )
    except Exception:  # noqa: BLE001 — 见 docstring：交不出节点由调用方降级
        raw = ""

    # `salvage_json` 而不是 `parse_json_object`：输出被推理挤到截断时，一份断在半句的 JSON
    # 会一个节点都不剩（实测过：12 个知识点的好答案 → 0 个 → 下游降级成研究计划）。
    # 它正常路径与 `parse_json_object` 逐字节一致，只在残缺时多救回已完整的那部分。
    parsed = salvage_json(raw)
    nodes = parsed.get("nodes") if isinstance(parsed, dict) else None
    nodes = [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []
    summary = str(parsed.get("summary") or "").strip() if isinstance(parsed, dict) else ""
    if not nodes:
        writer(StatusEvent(message="这一轮没有抽取出知识点"))
    writer(NodesEvent(nodes=nodes, summary=summary))
    # `state["report"]` 在这里存的是**一句话结论**：记忆召回（`memory/context.py`
    # 的 `_report_head`）与主题摘要都读这个字段，报告没了也得有东西可填。
    # 它同时是 DoneEvent 的正文——`_drive` 的兜底分支（`values["report"]`）因此照常成立。
    writer(DoneEvent(content=summary))
    return {"nodes": nodes, "report": summary}


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
    max_tool_rounds: int | None = None,
    extract_prompt: str | None = None,
):
    """现建现编译（每次运行独立，天然并发隔离）。

    checkpointer：None → MemorySaver（内存）；Phase 4 传入 SqliteSaver 时持久化到磁盘。
    kg_enabled（Phase 5）：在 evaluate（充分）与 synthesize 之间插入 kg 节点；
    禁用时图结构与 Phase 3/4 逐字节一致。mcp（Phase 6，默认 None）：research 节点
    透传 MCP 网关；None 时节点行为与 Phase 5 一致。

    extract_prompt（第六轮，默认 None）：**非空时收尾节点换成 `extract`**（按这个
    prompt 要一份 {nodes, summary} 的 JSON），而不是 `synthesize` 写报告。默认 None
    时图结构与 Phase 9 逐字节一致——`/api/chat`、eval、既有约 40 条图测试全都走在
    老路上，报告照旧生成。收尾节点是一个**参数**而不是一个布尔开关：「要不要抽取」
    与「按什么 prompt 抽取」在这里本来就是同一件事，两个参数会多出一种自相矛盾的
    组合（要抽取但没 prompt）。

    条件边只改**目标映射**（字面量 `"synthesize"` → 实际收尾节点），`route_after_evaluate`
    一行不动——它的返回值表达的是「研究够了，去收尾」，收尾是谁由这里决定。

    max_tool_rounds（Phase 9）经 partial 注入 research 节点，**不写进 ResearchState**：
    改被 checkpoint 序列化的 TypedDict 会让旧 checkpoint 反序列化时缺字段，而它本身就是
    每次运行固定的配置，不是会随迭代变化的状态。
    """
    builder = StateGraph(ResearchState)
    builder.add_node(
        "planner",
        partial(planner_node, llm=llm, search=search, rag=rag, memory_context=memory_context),
    )
    builder.add_node(
        "research",
        partial(
            research_node,
            llm=llm,
            search=search,
            rag=rag,
            mcp=mcp,
            max_tool_rounds=max_tool_rounds,
        ),
    )
    builder.add_node("evaluate", partial(evaluate_node, llm=llm, search=search, rag=rag))
    terminal = "extract" if extract_prompt else "synthesize"
    if extract_prompt:
        builder.add_node(
            "extract", partial(extract_node, llm=llm, system_prompt=extract_prompt)
        )
    else:
        builder.add_node(
            "synthesize", partial(synthesize_node, llm=llm, search=search, rag=rag)
        )
    if kg_enabled:
        builder.add_node("kg", partial(kg_node, llm=llm, kg_hops=kg_hops))
        builder.add_edge("kg", terminal)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "research")
    builder.add_edge("research", "evaluate")
    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"research": "research", "synthesize": "kg" if kg_enabled else terminal},
    )
    builder.add_edge(terminal, END)
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
    max_tool_rounds: int | None = None,  # Phase 9：research 节点每轮的工具调用轮次上限
    extract_prompt: str | None = None,  # 第六轮：非空则**不写报告**，按它抽节点（见 `_build_app`）
) -> AsyncIterator[object]:
    """驱动一次 LangGraph 研究任务，产出事件流（plan/status/tool/eval/memory/kg/nodes/done）。

    memory（Phase 4，默认 None 行为与 Phase 3 逐字节一致）：非空时——
    开跑前召回相关历史注入 planner（并先发 MemoryEvent 提示），流结束后把本次
    研究落库；同时图 checkpoint 持久化到 checkpoint_db（AsyncSqliteSaver，懒导入
    失败回退 MemorySaver）。MemorySaver 编译后要求 thread_id；图每次现建现编译，
    thread_id 每次唯一。

    kg_enabled（Phase 5，默认 False 行为与 Phase 4 逐字节一致）：在 evaluate 与
    synthesize 之间插入 kg 节点——从证据抽实体关系建内存图、按查询匹配实体并
    BFS 展开，把三元组注入报告 prompt。只在 graph 模式生效（loop 模式无 KG）。

    mcp（Phase 6，默认 None 行为与 Phase 5 逐字节一致）：MCP 网关，research 节点
    用它把 server 工具并入研究循环，MCP 输出经 notes 累加器在 synthesize 渲染。

    extract_prompt（第六轮，默认 None 行为与 Phase 9 逐字节一致）：非空时收尾节点是
    `extract`（发 `NodesEvent` 而不是写报告），供学习侧「只要图谱与关键词」的生成流程
    使用（见 `api/learning.py`）。
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
        "nodes": [],
    }
    config = {"configurable": {"thread_id": f"research-{uuid4().hex}"}}
    sink: dict = {}

    # 2) 编译图：memory 启用时用 **Async**SqliteSaver 持久化 checkpoint（懒导入，失败回退）。
    #    必须是 async 版：图由 `_drive` 的 `app.astream` 异步驱动，同步 SqliteSaver 的
    #    aget_tuple/aput 会直接抛 NotImplementedError（langgraph ≥1.0 起生效）——
    #    即「memory 开启的 graph 研究」整条链路不可用。
    saver_cm = None
    if memory is not None and checkpoint_db:
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            saver_cm = AsyncSqliteSaver.from_conn_string(checkpoint_db)
        except ImportError:
            saver_cm = None  # 未装 langgraph-checkpoint-sqlite / aiosqlite → 回退 MemorySaver

    if saver_cm is not None:
        async with saver_cm as saver:
            app = _build_app(
                llm=llm,
                search=search,
                rag=rag,
                memory_context=memory_context,
                checkpointer=saver,
                kg_enabled=kg_enabled,
                kg_hops=kg_hops,
                mcp=mcp,
                max_tool_rounds=max_tool_rounds,
                extract_prompt=extract_prompt,
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
            max_tool_rounds=max_tool_rounds,
            extract_prompt=extract_prompt,
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


async def _report_text(llm: LLMClient, prompt: list[dict], writer: Callable, *, max_tokens: int) -> str:
    """取报告全文：LLM 有 `stream_complete` 就逐字推 TokenEvent，否则退回 `complete`。

    **能力探测式降级**是本轮最关键的取舍。无条件改用流式会让
    `tests/test_api.py`、`test_agent_graph.py`、`test_kg_graph.py`、`test_mcp_graph.py`、
    `test_agent_eval_runner.py` 约 40 条断言（`complete_calls` 计数、`frames[-2]` 的
    事件序列）全部需要改写——它们依赖 Fake 客户端「用 complete 产出报告」。
    而 Fake 与 `agent/eval/real.py::CountingChatClient` 都没有 `stream_complete`，
    探测即降级 → 那些测试一行不用动，真实评测的调用计数口径也不漂移；只有生产
    `ChatClient` / `ModelGateway` 实现了它，用户才看到报告逐字到达。

    不变量：返回值恒等于**推出去的 token 拼接**，故前端「token 优先、done 兜底」
    两种渲染路径结果一致；`DoneEvent` 仍由 synthesize_node 发出 → `_drive` 的
    `saw_done` 与 checkpoint 兜底分支一行不动。

    实现搬去了 `llm/streaming.py::stream_text`（Phase 9 的节点讲解也要同一套判断），
    这里只把「每段回吐 → TokenEvent」这一步接上。
    """
    return await stream_text(
        llm, prompt, max_tokens=max_tokens,
        on_delta=lambda delta: writer(TokenEvent(delta)),
    )


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
