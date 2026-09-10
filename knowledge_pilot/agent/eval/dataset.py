"""Agent Evaluation 数据集：JSON schema 校验与加载（纯 stdlib，零 langgraph/mcp 依赖）。

一个 item = 一个独立的端到端研究任务。JSON 格式（离线与 --real 共用同一 schema）：
{
  "items": [
    {
      "query": "研究问题",
      "must_include": ["GraphRAG", "知识图谱"],   // 最终报告需覆盖的词（rubric 判定）
      "required_tools": ["search_web"],           // 必须至少调用一次的工具（选择精度分母）
      "expected_tool_calls": [                    // 期望工具调用轨迹（参数精度基准）
        {"name": "search_web", "arguments": {"query": "..."}}
      ],
      "offline_profile": "ideal",                 // 离线脚本 profile；--real 忽略（见 offline.py）
      "variants": ["loop", "graph"],              // 变体过滤；缺省 None = 全部五档
      "pre_seed": [{"query": "...", "report": "...", "sources": [...]}],  // memory 变体预置历史
      "aspects": [{"name": "cites_sources", "question": "..."}]            // --real LLM judge 维度
    }
  ]
}

约定：
- 加载只做结构性校验（类型 / 必填 / 枚举），语义判定交给 metrics / offline / judge。
- **variants / offline_profile 的合法值定义在本模块**（白名单 frozenset），offline.py
  的 ALL_VARIANTS 与脚本库由这些名字派生——避免「dataset ↔ offline」循环 import，
  又保证单一事实来源（名字漂移在加载期就被位置化 ValueError 拦下）。
- item.variants 为 None = 跑全部五档变体；某变体实际条目数 ≤ 全量（表格 n 列体现）。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 五档对比变体名（offline.py ALL_VARIANTS 从这些名字派生 driver/memory/kg/mcp 开关）。
KNOWN_VARIANT_NAMES = frozenset({"loop", "graph", "graph+memory", "graph+kg", "all"})

# 离线脚本 profile（offline.py 脚本库键名；--real 忽略）。
VALID_PROFILES = frozenset(
    {"ideal", "iterate_to_cap", "tool_round_cap", "planner_bad_json", "mcp_only_tool"}
)


@dataclass(frozen=True)
class GoldToolCall:
    """期望工具调用的「金标准」：名字 + 参数。

    名字是选择精度的分母项；参数是参数精度基准——同一名字可能有多个期望调用
    （如多轮 search_web），判定时与观测到的同名调用按命中键数最优贪心对齐。
    """

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentAspect:
    """--real LLM judge 的逐条判定维度（仅真实模式消费；离线不判质量）。

    例：{"name": "cites_sources", "question": "报告是否给出带 URL 的来源引用？"}
    """

    name: str
    question: str


@dataclass(frozen=True)
class SeedRun:
    """memory 变体预置的历史研究（一次 save_run 的快照）。

    sources 形状与 memory.save_run 的 sources 一致：list[{"title", "url"}]。
    预置后 graph 的 memory.search 能在开跑前命中 → MemoryEvent(found≥1)。
    """

    query: str
    report: str
    sources: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class AgentItem:
    """一条端到端研究评测任务。"""

    query: str
    must_include: tuple[str, ...] = ()          # rubric 覆盖词（空 → coverage 恒 1.0）
    required_tools: tuple[str, ...] = ()        # 必须调用的工具（选择精度分母）
    expected_tool_calls: tuple[GoldToolCall, ...] = ()
    offline_profile: str = "ideal"              # 离线脚本 profile（键控 offline.py 脚本库）
    variants: tuple[str, ...] | None = None     # None = 全部 KNOWN_VARIANT_NAMES
    pre_seed: tuple[SeedRun, ...] = ()
    aspects: tuple[AgentAspect, ...] = ()


@dataclass(frozen=True)
class AgentDataset:
    items: tuple[AgentItem, ...]


def _require_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} 必须是非空字符串，得到: {value!r}")
    return value


def _require_str_list(data: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    """可选字符串数组；缺省空元组。"""
    value = data.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(s, str) and s.strip() for s in value
    ):
        raise ValueError(f"{where}.{key} 必须是字符串数组，得到: {value!r}")
    return tuple(value)


def load_dataset(path: str | Path) -> AgentDataset:
    """加载并校验 Agent Evaluation 数据集；schema 不合法抛 ValueError（带位置信息）。"""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取数据集 {path}: {exc}") from exc

    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise ValueError("数据集必须包含非空的 items 数组")

    items: list[AgentItem] = []
    for i, item_raw in enumerate(items_raw):
        where = f"items[{i}]"
        if not isinstance(item_raw, dict):
            raise ValueError(f"{where} 必须是对象")
        query = _require_str(item_raw, "query", where)
        must_include = _require_str_list(item_raw, "must_include", where)
        required_tools = _require_str_list(item_raw, "required_tools", where)

        expected_raw = item_raw.get("expected_tool_calls", [])
        if not isinstance(expected_raw, list) or not all(
            isinstance(e, dict) for e in expected_raw
        ):
            raise ValueError(f"{where}.expected_tool_calls 必须是对象数组")
        expected: list[GoldToolCall] = []
        for j, e in enumerate(expected_raw):
            e_where = f"{where}.expected_tool_calls[{j}]"
            name = _require_str(e, "name", e_where)
            arguments = e.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError(f"{e_where}.arguments 必须是对象，得到: {arguments!r}")
            expected.append(GoldToolCall(name=name, arguments=dict(arguments)))

        profile = item_raw.get("offline_profile", "ideal")
        if not isinstance(profile, str) or profile not in VALID_PROFILES:
            raise ValueError(
                f"{where}.offline_profile 必须是 {sorted(VALID_PROFILES)} 之一，"
                f"得到: {profile!r}"
            )

        variants_raw = item_raw.get("variants")
        variants: tuple[str, ...] | None = None
        if variants_raw is not None:
            if not isinstance(variants_raw, list) or not all(
                isinstance(v, str) for v in variants_raw
            ):
                raise ValueError(f"{where}.variants 必须是字符串数组或省略")
            for v in variants_raw:
                if v not in KNOWN_VARIANT_NAMES:
                    raise ValueError(
                        f"{where}.variants 含未知变体 {v!r}，"
                        f"合法值: {sorted(KNOWN_VARIANT_NAMES)}"
                    )
            variants = tuple(variants_raw)

        seeds: list[SeedRun] = []
        for j, s_raw in enumerate(item_raw.get("pre_seed", [])):
            s_where = f"{where}.pre_seed[{j}]"
            if not isinstance(s_raw, dict):
                raise ValueError(f"{s_where} 必须是对象")
            seed_query = _require_str(s_raw, "query", s_where)
            seed_report = _require_str(s_raw, "report", s_where)
            sources_raw = s_raw.get("sources", [])
            if not isinstance(sources_raw, list) or not all(
                isinstance(s, dict) for s in sources_raw
            ):
                raise ValueError(f"{s_where}.sources 必须是对象数组")
            source_dicts: list[dict[str, str]] = []
            for k, src in enumerate(sources_raw):
                src_where = f"{s_where}.sources[{k}]"
                source_dicts.append(
                    {"title": _require_str(src, "title", src_where), "url": _require_str(src, "url", src_where)}
                )
            seeds.append(
                SeedRun(query=seed_query, report=seed_report, sources=tuple(source_dicts))
            )

        aspects: list[AgentAspect] = []
        for j, a_raw in enumerate(item_raw.get("aspects", [])):
            a_where = f"{where}.aspects[{j}]"
            if not isinstance(a_raw, dict):
                raise ValueError(f"{a_where} 必须是对象")
            aspects.append(
                AgentAspect(
                    name=_require_str(a_raw, "name", a_where),
                    question=_require_str(a_raw, "question", a_where),
                )
            )

        items.append(
            AgentItem(
                query=query,
                must_include=must_include,
                required_tools=required_tools,
                expected_tool_calls=tuple(expected),
                offline_profile=profile,
                variants=variants,
                pre_seed=tuple(seeds),
                aspects=tuple(aspects),
            )
        )

    return AgentDataset(items=tuple(items))
