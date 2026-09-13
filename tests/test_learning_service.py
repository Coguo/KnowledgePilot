"""Phase 9 M4 + 第六轮：编排层把「研究产出」变成「学习图」的两条路。

两条路的**返回契约一样**（`(graph, degraded_reason)`），取舍却相反：

- `build_graph_from_material`（第六轮主路径，`agent_mode="graph"`）—— 纯函数、零 LLM。
  第三级刻意**不给单节点兜底**，宁可交白卷让调用方落 `failed`；
- `build_graph_from_report`（`agent_mode="loop"`）—— 报告为空时同上，报告非空时才降级
  到标题。

所以这里测的不是「能不能建出图」，而是**每一级分别降到哪里、以及什么情况下必须交白卷**。
"""

import json

from knowledge_pilot.learning import service
from knowledge_pilot.learning.service import (
    DEGRADED_HEADINGS,
    DEGRADED_PLAN,
    NOTHING_TO_BUILD,
    build_graph_from_material,
    build_graph_from_report,
)

EXTRACTED = [
    {"name": "文本切分", "type": "技术", "summary": "切块",
     "key_points": ["定长"], "prerequisites": [], "order": 1},
    {"name": "向量检索", "type": "技术", "summary": "近邻",
     "key_points": ["embedding"], "prerequisites": ["文本切分"], "order": 2},
]
PLAN = [
    {"title": "文本切分", "purpose": "打基础"},
    {"title": "向量检索", "purpose": "接着学"},
]
REPORT = "## 文本切分\n\n正文\n\n## 向量检索\n\n正文\n"


class _LLM:
    """最小假客户端（只服务 `build_graph_from_report` 那一级抽取）。"""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        self.calls += 1
        return self.reply


# ---- build_graph_from_material：第一级 ---------------------------------


def test_extracted_nodes_become_the_graph_without_degrading():
    graph, degraded = build_graph_from_material(EXTRACTED, plan=None, max_nodes=12)
    assert degraded == ""
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索"]
    assert len(graph["edges"]) == 1


def test_max_nodes_caps_the_extracted_path():
    graph, _ = build_graph_from_material(EXTRACTED, plan=PLAN, max_nodes=1)
    assert [n["name"] for n in graph["nodes"]] == ["文本切分"]


# ---- build_graph_from_material：第二级（计划兜底） ----------------------


def test_no_nodes_falls_back_to_the_plan():
    graph, degraded = build_graph_from_material([], plan=PLAN, max_nodes=12)
    assert degraded == DEGRADED_PLAN
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索"]
    # 降级出来的节点是「章节」且没有关键词：资料里没抽到，就别假装抽到了
    assert all(n["type"] == "章节" and n["key_points"] == [] for n in graph["nodes"])


def test_malformed_nodes_also_fall_back_to_the_plan():
    """抽回来一堆没有名字的东西 == 没抽到（`build_learning_graph` 会全丢掉）。"""
    junk = [{"name": "", "summary": "x"}, None, "不是字典"]
    graph, degraded = build_graph_from_material(junk, plan=PLAN, max_nodes=12)
    assert degraded == DEGRADED_PLAN
    assert len(graph["nodes"]) == 2


# ---- build_graph_from_material：第三级（交白卷） ------------------------


def test_nothing_at_all_yields_an_empty_graph_and_a_reason():
    """**刻意不给单节点兜底**——那正是用户报的故障（看着像成功、什么也学不了）。"""
    for nodes, plan in ([], None), ([], []), (None, None), ([], [{"title": " "}]):
        graph, degraded = build_graph_from_material(nodes, plan, max_nodes=12)
        assert graph == {"nodes": [], "edges": []}, (nodes, plan)
        assert degraded == NOTHING_TO_BUILD


def test_the_reason_is_non_empty_whenever_the_graph_is_empty():
    """调用方靠「图空了 + 有原因」落 failed；原因空着会让失败信息变成一句空白。"""
    graph, degraded = build_graph_from_material([], None, max_nodes=12)
    assert not graph["nodes"] and degraded


# ---- build_graph_from_report：loop 模式的老路 --------------------------


async def test_report_extraction_succeeds_without_degrading():
    llm = _LLM(json.dumps({"nodes": EXTRACTED}, ensure_ascii=False))
    graph, degraded = await build_graph_from_report(
        llm, REPORT, max_nodes=12, topic_title="RAG"
    )
    assert degraded == "" and llm.calls == 1
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索"]


async def test_report_falls_back_to_headings_when_extraction_fails():
    llm = _LLM("模型今天不想输出 JSON")
    graph, degraded = await build_graph_from_report(
        llm, REPORT, max_nodes=12, topic_title="RAG"
    )
    assert degraded == DEGRADED_HEADINGS
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索"]


async def test_an_empty_report_yields_an_empty_graph_not_a_single_node():
    """空报告 = 推理模型吃光预算的典型症状。以前这里会造一个单节点图。"""
    llm = _LLM(json.dumps({"nodes": EXTRACTED}, ensure_ascii=False))
    for report in ("", "   ", "\n\n"):
        graph, degraded = await build_graph_from_report(
            llm, report, max_nodes=12, topic_title="RAG"
        )
        assert graph == {"nodes": [], "edges": []}, report
        assert degraded == NOTHING_TO_BUILD
    assert llm.calls == 0, "报告本来就是空的，不该为它烧一次调用"


async def test_a_report_without_headings_still_produces_a_single_node():
    """报告**有正文**但连标题都没有 → 保住「至少有一个点」（与空报告不同）。"""
    llm = _LLM("不是 JSON")
    graph, degraded = await build_graph_from_report(
        llm, "一段没有任何标题的普通文字。", max_nodes=12, topic_title="RAG 入门"
    )
    assert degraded == DEGRADED_HEADINGS
    assert [n["name"] for n in graph["nodes"]] == ["RAG 入门"]


# ---- 文案与常量 --------------------------------------------------------


def test_degraded_reasons_say_what_happened_and_what_you_got():
    """这两句话会原样出现在左栏日志里——用户据此判断「要不要重新生成」。"""
    assert "计划" in DEGRADED_PLAN and "线性" in DEGRADED_PLAN
    assert "标题" in DEGRADED_HEADINGS and "线性" in DEGRADED_HEADINGS
    assert DEGRADED_PLAN != DEGRADED_HEADINGS
    assert service.NOTHING_TO_BUILD == NOTHING_TO_BUILD
