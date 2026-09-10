"""Agent Evaluation 离线确定性组件：脚本化 LLM / 桩搜索 / 桩 MCP / 五档变体 / 脚本库。

诚实边界（写进 docs 的口径）：
- 离线 LLM 是**脚本化的**——`ScriptedChatClient` 按 profile 预先写好每一步返回什么。
  因此离线**不能**回答「哪个变体的报告质量更好」：canonical report 由 make_canonical_report
  从 item.must_include 拼出，所有跑通路径的 coverage 都=1。离线区分的是**机制与系统指标**：
  脚本扰动（tool_round_cap / planner_bad_json / iterate_to_cap / mcp_only_tool）下能否完成、
  工具调用轨迹、complete/stream 调用次数、错误率、memory/kg 事件落点、延迟与 token 成本。
  **质量与「是否真的更好」交给 --real + LLM judge。**
- 脚本化的"模型"只会执行 item.expected_tool_calls 的第一个工具（多工具长轨迹不在离线范围，
  那是 --real 的活）。要让 observed 参数与 gold 对齐，离线直接复用 gold 的 arguments 当
  模型动作——offline 测的是「机制能跑通 + 我们能测出偏差」，不是模型判断力。
- mcp_only_tool profile：脚本让模型调用 search_papers——**只在带 mcp 的 `all` 变体存在**，
  其余四档无此工具 → `_dispatch_tool` 抛 ValueError → 整次 run 报错（离线人为构造：
  真实模型不会调用 tools= 未暴露的工具，docs 注明这是工具可用性轴的构造性演示）。

本模块不 import langgraph / graph / loop（纯 stdlib + base 依赖；graph driver 由 runner
懒导入）。与 tests/fakes.py 分层：这里复制脚本化机制，**不 import tests**。
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from knowledge_pilot.agent.eval.dataset import AgentItem, VALID_PROFILES  # noqa: F401  白名单再导出
from knowledge_pilot.llm.client import StreamChunk
from knowledge_pilot.memory.store import ResearchMemoryStore
from knowledge_pilot.rag.eval.metrics import est_tokens
from knowledge_pilot.search.stub import StubSearchProvider

# 与数据集白名单必须一致（dataset.KNOWN_VARIANT_NAMES）；名字是唯一事实来源，
# 这里只做派生，测试里 assert ALL_VARIANTS.keys() == KNOWN_VARIANT_NAMES 防漂移。
_KNOWN = frozenset({"loop", "graph", "graph+memory", "graph+kg", "all"})


@dataclass(frozen=True)
class Variant:
    """一档被测配置：driver（loop 单轮循环 / graph 编排）+ 三组可插拔能力开关。"""

    name: str
    driver: Literal["loop", "graph"]
    use_memory: bool = False
    use_kg: bool = False
    use_mcp: bool = False


ALL_VARIANTS: dict[str, Variant] = {
    "loop": Variant(name="loop", driver="loop"),
    "graph": Variant(name="graph", driver="graph"),
    "graph+memory": Variant(name="graph+memory", driver="graph", use_memory=True),
    "graph+kg": Variant(name="graph+kg", driver="graph", use_kg=True),
    "all": Variant(
        name="all", driver="graph", use_memory=True, use_kg=True, use_mcp=True
    ),
}

assert set(ALL_VARIANTS) == _KNOWN, "offline 变体名与 dataset 白名单不一致"


# ---- 脚本化 LLM ------------------------------------------------------------


@dataclass
class ScriptedRound:
    """流式对话的「一轮」：content（无工具则视为最终回答）或一组工具调用。"""

    content: str | None = None
    tool_calls: tuple[tuple[str, dict], ...] = ()


def content_round(text: str) -> ScriptedRound:
    return ScriptedRound(content=text)


def tool_round(name: str, arguments: dict) -> ScriptedRound:
    return ScriptedRound(tool_calls=((name, arguments),))


class ScriptedChatClient:
    """脚本化 LLM：stream 按轮次、complete 按序，越界重复最后一条（防循环兜底）。

    镜像 tests/fakes.FakeChatClient 的机制（tool_call 名称一次 + 参数两半切分发，验证
    增量累加），**生产内复制、不 import tests**。额外记账给系统指标：
    - stream_calls / complete_calls：图 vs 循环、多轮自评的核心区分轴。
    - tokens：每次调用按 messages 内容长度估算的累计成本（context 逐步增长的贴合）。
    - seen_messages / seen_tools / response_formats：供测试断言与图漂移 canary。
    """

    model = "scripted"

    def __init__(
        self,
        stream_script: list[ScriptedRound],
        complete_script: list[str] | None = None,
    ) -> None:
        self.stream_script = list(stream_script)
        self.complete_script = list(complete_script or [])
        self.stream_calls = 0
        self.complete_calls = 0
        self.tokens = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list[list[dict]] = []
        self.seen_response_formats: list[dict] = []

    # ---- 记账 -------------------------------------------------------------

    def _record_call(self, messages: list[dict], tools: list[dict] | None) -> None:
        self.seen_messages.append(list(messages))
        self.seen_tools.append(list(tools) if tools else None)
        self.tokens += estimate_messages_tokens(messages)

    # ---- LLMClient Protocol ------------------------------------------------

    async def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        self._record_call(messages, None)
        self.seen_response_formats.append(response_format)
        if self.complete_script:
            idx = min(self.complete_calls, len(self.complete_script) - 1)
            self.complete_calls += 1
            return self.complete_script[idx]
        self.complete_calls += 1
        return ""

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._record_call(messages, tools)
        if self.stream_script:
            round_ = self.stream_script[min(self.stream_calls, len(self.stream_script) - 1)]
        else:
            round_ = ScriptedRound(content="")
        self.stream_calls += 1

        if round_.content is not None:
            yield StreamChunk(content_delta=round_.content)
        for index, (name, arguments) in enumerate(round_.tool_calls):
            args_text = json.dumps(arguments or {}, ensure_ascii=False)
            # 名称一次到位、参数分两半切分：验证调用方增量累加逻辑。
            yield StreamChunk(
                tool_call_delta={
                    "index": index,
                    "id": f"call_{index}",
                    "name": name,
                    "arguments": None,
                }
            )
            mid = len(args_text) // 2
            yield StreamChunk(
                tool_call_delta={"index": index, "arguments": args_text[:mid]}
            )
            yield StreamChunk(
                tool_call_delta={"index": index, "arguments": args_text[mid:]}
            )


def estimate_messages_tokens(messages: list[dict]) -> int:
    """单次请求的 context token 启发式（复用 rag/eval 口径）：各消息文本字符数/2。

    含 assistant 的 tool_call 参数串（模型发出的内容也要计费），不含没文本的纯系统/角色。
    公共（非私有）：--real 的 CountingChatClient 也用它记账，保证离线/真实两种模式
    的 token 成本用同一口径估算（跨模式可比）。
    """
    total = 0
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str) and content:
            total += est_tokens(content)
        for tc in (m.get("tool_calls") or []):
            args = ((tc.get("function") or {}).get("arguments")) or ""
            if args:
                total += est_tokens(args)
    return max(1, total)


# ---- 桩搜索 / 桩 MCP -------------------------------------------------------

# MCP 桩默认工具 = memory + papers 两个 server 的只读工具（schema 镜像，供 seen_tools/
# 有效工具列表断言；LLM 行为不受影响——行为由脚本决定）。
_MCP_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "关键词召回用户历史研究记录（只读）。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_research",
            "description": "最近的研究记录（只读）。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "arXiv 论文检索（只读，无 key）。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


class StubMCPGateway:
    """鸭子类型 MCP 网关（5 方法，同 FakeMCPGateway / 网关接口）：固定工具 + canned 响应。

    结果文本含 arXiv/stub URL，供「MCP 输出 → notes → 报告补充资料、不进 evidence/来源」
    语义红线在端到端层面成立（离线文本不判，但调用轨迹可断言）。
    """

    def __init__(self, tools: list[dict] | None = None) -> None:
        self._tools = list(tools if tools is not None else _MCP_TOOLS)
        self.calls: list[tuple[str, dict]] = []

    def names(self) -> list[str]:
        return [t["function"]["name"] for t in self._tools]

    def has(self, name: str) -> bool:
        return any(t["function"]["name"] == name for t in self._tools)

    def tool_schemas(self) -> list[dict]:
        return list(self._tools)

    def prompt_hint(self) -> str:
        return "可用 MCP 辅助工具（结果仅供补充参考）：search_memory / recent_research / search_papers"

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, dict(arguments or {})))
        canned = {
            "search_memory": "历史记忆中未找到更相关条目（桩返回）。",
            "recent_research": "最近研究记录（桩）：GraphRAG 综述（2026-09-01）。",
            "search_papers": (
                "arXiv 检索结果（桩）：\n"
                "- Agentic RAG: Self-Reflective Retrieval（https://arxiv.org/abs/2412.00001）\n"
                "- Tool-Use Agents for RAG（https://arxiv.org/abs/2412.00002）"
            ),
        }
        return canned.get(name, "（桩工具无返回内容）")


# ---- canonical 报告（离线唯一"真话来源"：报告质量不判，覆盖词必须到齐） -----


def make_canonical_report(item: AgentItem) -> str:
    """把 item 拼成确定性报告：must_include 词各自成行，保证词边界可命中。

    词边界注意：拉丁词（GraphRAG/RAG/arXiv）单独成行 → 前后是换行非词字符，\b 命中；
    中文词直接子串命中；带空格的混合词（agentic RAG、RAG 优化）按原样成行。
    """
    parts = ["# 研究报告", ""]
    parts.append(f"## 摘要")
    parts.append(f"针对研究问题「{item.query}」，综合搜索结果给出如下结论。")
    if item.must_include:
        parts += ["", "## 关键要点"]
        parts += [f"- {term}" for term in item.must_include]
    parts += ["", "## 来源", "- [1] 离线评测桩来源（https://stub.example/offline-source）"]
    return "\n".join(parts)


# ---- 脚本 profile 库（由常量推导精确长度；graph 改动会显式打破 canary） -----

_BAD_PLANNER_RAW = "这不是 JSON——planner 解析失败应回退到单步计划，而不是报错。"
_EVAL_SUFF = json.dumps(
    {"sufficient": True, "reason": "资料已充分", "gap": ""}, ensure_ascii=False
)
_EVAL_INSUFF = json.dumps(
    {"sufficient": False, "reason": "资料仍不充分", "gap": "请补充更多直接资料"},
    ensure_ascii=False,
)
_KG_RAW = json.dumps(
    {
        "entities": [
            {"name": "RAG", "type": "concept"},
            {"name": "GraphRAG", "type": "concept"},
        ],
        "relations": [
            {"source": "RAG", "target": "GraphRAG", "relation": "related"}
        ],
    },
    ensure_ascii=False,
)

# MCP 桩工具名（其输出是自由文本、不进 evidence → 触发 kg 节点「证据空→不调 complete」）。
_MCP_TOOL_NAMES = frozenset({"search_memory", "recent_research", "search_papers"})

# 研究节点内层 run_research 每轮流式模型的默认内容轮文本（图模式会丢弃过程 token）。
_SEARCH_SUMMARY = "本轮已通过工具收集到直接相关资料，信息足以支撑后续综合。"


def _tool_call(item: AgentItem) -> tuple[str, dict]:
    """脚本模型的第一动作：复用第一条期望工具调用（名称+参数），保证 offline 参数精度=1；
    无期望调用时回退一次 search_web（查询用 item.query）。"""
    if item.expected_tool_calls:
        gold = item.expected_tool_calls[0]
        return gold.name, dict(gold.arguments)
    return "search_web", {"query": item.query}


def _plan_json(item: AgentItem) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "title": "综合研究",
                    "question": item.query,
                    "purpose": "回答用户的研究问题",
                }
            ]
        },
        ensure_ascii=False,
    )


@dataclass(frozen=True)
class ScriptPlan:
    """一份完整脚本：stream（逐轮）+ complete（按序）+ canonical 报告。"""

    stream: list[ScriptedRound]
    complete: list[str]
    report: str


def _has_evidence_producing_search(item: AgentItem) -> bool:
    """脚本会不会产生「结构化网页证据」？

    graph 的 kg 节点只在 evidence 非空时才调 LLM 抽实体（空证据直接发 KgEvent(0,0,0)
    返回）。决定 complete 脚本要不要预留 kg 条目、以及 kg 条目在 complete 中的位置——
    否则「kg 未消费 → synth 会吃到 kg 的 JSON」索引错位。
    """
    return _tool_call(item)[0] == "search_web"


def build_script_plan(
    item: AgentItem, variant: Variant, *, max_iterations: int = 3
) -> ScriptPlan:
    """按 (item, variant) 推导精确脚本。

    profile 来自 item.offline_profile；loop driver 只吃 stream（循环无 planner/eval/kg/
    synth 的 complete），graph driver 补 complete 序列。iterate_to_cap 的完整序列长度
    = 1(planner) + max_iterations(evaluate) + [1(kg)] + 1(synthesize)——canary 断言防
    graph 改动悄悄改变调用数。
    """
    report = make_canonical_report(item)
    name, arguments = _tool_call(item)
    search_round = tool_round(name, arguments)

    if variant.driver == "loop":
        # 单轮循环：没有自评/综合，iterate 等 profile 对 loop 退化为一轮「搜+答」。
        if item.offline_profile == "tool_round_cap":
            # 模型永远只请求工具、从不给内容 → 循环撞 MAX_TOOL_ROUNDS，回答为空。
            stream = [search_round]
        else:
            stream = [search_round, content_round(report)]
        return ScriptPlan(stream=stream, complete=[], report=report)

    # ---- graph：planner → research×n → evaluate×n → [kg] → synthesize -----
    profile = item.offline_profile
    if profile == "planner_bad_json":
        planner_entry = _BAD_PLANNER_RAW
    else:
        planner_entry = _plan_json(item)

    if profile == "iterate_to_cap":
        n_iterations = max_iterations
        evals = [_EVAL_INSUFF] * max_iterations
    else:
        n_iterations = 1
        evals = [_EVAL_SUFF]

    if profile == "tool_round_cap":
        # 模型每轮都请求工具：research 内层 run_research 撞 MAX_TOOL_ROUNDS(=4) 才停，
        # 但执行的 3 次搜索已产生证据 → evaluate 判充分 → 仍能综合出报告。
        research_stream = [search_round]
    elif profile == "iterate_to_cap":
        # 每轮研究 = 1 次搜索 + 1 次总结（图会丢弃总结 token），× max_iterations。
        research_stream = [search_round, content_round(_SEARCH_SUMMARY)] * n_iterations
    else:
        research_stream = [search_round, content_round(_SEARCH_SUMMARY)]

    complete: list[str] = [planner_entry, *evals]
    # kg 只在「研究会产生证据」时预留条目（否则证据空 → kg 节点不消费 complete，
    # 索引会错位，synth 会吃到 kg 的 JSON）。
    if variant.use_kg and _has_evidence_producing_search(item):
        complete.append(_KG_RAW)
    complete.append(report)

    return ScriptPlan(stream=research_stream, complete=complete, report=report)


# ---- 离线组件工厂（runner 消费；duck typing，与 real 组件同形状） -------------


@dataclass
class PreparedRun:
    """一次 (item, variant) 运行的完整组件；close() 释放 sqlite/临时目录。"""

    llm: ScriptedChatClient
    search: StubSearchProvider
    memory: ResearchMemoryStore | None = None
    mcp: StubMCPGateway | None = None
    _tmpdir: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    def close(self) -> None:
        if self.memory is not None:
            self.memory.close()
        if self._tmpdir is not None:
            self._tmpdir.cleanup()


class _OfflineComponents:
    """离线组件：每个 (item, variant) 现建全新脚本化 LLM + 桩搜索 + 记忆库/MCP。"""

    def make_judge(self):
        from knowledge_pilot.agent.eval.judge import RubricJudge

        return RubricJudge()

    def make_context(
        self, item: AgentItem, variant: Variant, *, max_iterations: int
    ) -> PreparedRun:
        plan = build_script_plan(item, variant, max_iterations=max_iterations)
        llm = ScriptedChatClient(plan.stream, plan.complete)

        memory: ResearchMemoryStore | None = None
        tmpdir: tempfile.TemporaryDirectory | None = None
        if variant.use_memory:
            # 每 (variant, item) 独立全新记忆库（绝不跨 item 共享）——隔离保逐字节确定，
            # 同 rag/eval「每 (spec, item) 独立建库」纪律。pre_seed 预置后即用即弃。
            tmpdir = tempfile.TemporaryDirectory(prefix="kp_eval_mem_")
            store = ResearchMemoryStore(os.path.join(tmpdir.name, "memory.db"))
            for seed in item.pre_seed:
                store.save_run(
                    seed.query, report=seed.report, sources=list(seed.sources)
                )
            memory = store
        mcp = StubMCPGateway() if variant.use_mcp else None
        return PreparedRun(
            llm=llm, search=StubSearchProvider(), memory=memory, mcp=mcp, _tmpdir=tmpdir
        )

    def make_warmup_context(self, variant: Variant) -> PreparedRun | None:
        """graph 变体的首跑预热：吸收懒导入/首编译（结果丢弃，不测延迟）。"""
        if variant.driver != "graph":
            return None
        warm = ScriptPlan(
            stream=[
                tool_round("search_web", {"query": "预热"}),
                content_round("warmup"),
            ],
            complete=[
                _plan_json(AgentItem(query="warmup")),
                _EVAL_SUFF,
                "warmup report",
            ],
            report="warmup report",
        )
        return PreparedRun(
            llm=ScriptedChatClient(warm.stream, warm.complete),
            search=StubSearchProvider(),
            mcp=StubMCPGateway() if variant.use_mcp else None,
        )


def make_offline_components():
    """离线组件工厂（默认，零 key / 零重依赖，纯 stdlib + base 依赖）。"""
    return _OfflineComponents()
