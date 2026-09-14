"""第六轮：学习侧收尾节点 `extract_node` —— **从资料抽知识点，不写报告**。

它替代的是一条真实故障链（`synthesize` 写长报告 → 推理模型把 `max_tokens` 吃光 →
正文一个字都没有 → 学习侧降级成「一个节点」的图）。所以这里测的重点不是「抽得好不好」，
而是两条：**收尾确实换成了抽取**（默认参数下不换），以及**抽取失败时交白卷而不是抛异常**
（交白卷会由调用方降级到研究计划，抛异常则把已经跑完的研究一起废掉）。
"""

import json

from knowledge_pilot.agent.events import (
    DoneEvent,
    NodesEvent,
    StatusEvent,
    TokenEvent,
)
from knowledge_pilot.agent.graph import (
    EXTRACT_MAX_TOKENS,
    SYNTHESIZE_PROMPT,
    run_research_graph,
)
from knowledge_pilot.learning.path import LEARNING_PATH_PROMPT
from knowledge_pilot.llm.json_utils import parse_json_object
from knowledge_pilot.llm.providers import THINKING_OFF
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient

PLANNER_JSON = '{"steps": [{"title": "A", "question": "子问题A", "purpose": "p"}]}'
EVAL_SUFFICIENT = '{"sufficient": true, "reason": "资料足够", "gap": ""}'
REPORT = "# 研究报告\n这是最终报告。"
NODES_JSON = json.dumps(
    {
        "nodes": [
            {"name": "文本切分", "type": "技术", "summary": "切块",
             "key_points": ["定长"], "prerequisites": [], "order": 1},
            {"name": "向量检索", "type": "技术", "summary": "近邻",
             "key_points": ["embedding"], "prerequisites": ["文本切分"], "order": 2},
        ],
        "summary": "围绕切分与检索的学习路径",
    },
    ensure_ascii=False,
)
# 研究节点：无工具直答（证据为空，用来验「暂无资料」那条分支）
SCRIPT_DIRECT = [(["研究完成。"], [])]


class _ExtractFails(FakeChatClient):
    """只有**抽取**那一次调用炸掉（模拟上游 500）——planner/evaluate 照常。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.raised = 0  # 断言「确实炸过」：否则这条用例在「压根没触发」时也会绿

    async def complete(self, messages, *, max_tokens=None, response_format=None, extra_body=None):
        if messages and "学习路径" in str(messages[0].get("content", "")):
            self.complete_calls += 1
            self.raised += 1
            raise RuntimeError("上游 500")
        return await super().complete(
            messages,
            max_tokens=max_tokens,
            response_format=response_format,
            extra_body=extra_body,
        )


async def _run(query, llm, **kwargs):
    return [
        e
        async for e in run_research_graph(
            query,
            llm=llm,
            search=StubSearchProvider(),
            rag=None,
            max_iterations=1,
            **kwargs,
        )
    ]


def _fake(complete_script, stream_script=SCRIPT_DIRECT):
    llm = FakeChatClient(script=stream_script)
    llm.complete_script = complete_script
    return llm


def _extract_prompt_seen(llm):
    """抽取那一次调用收到的 system prompt（planner/evaluate/extract 三次里的最后一次）。"""
    return llm.seen_messages[-1][0]["content"]


# ---- 收尾换成抽取 -------------------------------------------------------


async def test_extract_mode_returns_nodes_instead_of_a_report():
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, NODES_JSON])
    events = await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    types = [type(e).__name__ for e in events]
    assert "NodesEvent" in types
    assert not any(isinstance(e, TokenEvent) for e in events), "JSON 不该逐字吐给用户"

    nodes_event = next(e for e in events if isinstance(e, NodesEvent))
    assert [n["name"] for n in nodes_event.nodes] == ["文本切分", "向量检索"]
    # 清洗与建图是 learning 层的事：这里交出去的是**原始** dict（一个字段都没动）
    assert nodes_event.nodes[0]["key_points"] == ["定长"]
    assert nodes_event.summary == "围绕切分与检索的学习路径"

    assert events[-1].content == nodes_event.summary  # DoneEvent 的正文 = 那句话结论
    assert [e.message for e in events if isinstance(e, StatusEvent)] == [
        "正在研究，收集资料…",
        "正在从资料中抽取知识点…",
    ]
    assert llm.complete_calls == 3  # planner + evaluate + extract（与 synthesize 同数）


async def test_extract_uses_the_injected_prompt_with_a_wide_budget():
    """prompt 由调用方注入（agent 层不认识「知识点」），预算比 synthesize 宽。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, NODES_JSON])
    await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    assert _extract_prompt_seen(llm) == LEARNING_PATH_PROMPT
    assert llm.seen_response_formats[-1] == {"type": "json_object"}
    assert EXTRACT_MAX_TOKENS == 16384 and EXTRACT_MAX_TOKENS > 4096


async def test_extract_turns_thinking_off_and_only_on_that_call():
    """抽取这一次调用带上「关掉思考」的方言；planner/evaluate 不带。

    这是第七轮那个 bug 的**唯一**机械保证。推理模型的 `reasoning_content` 与正文共用
    `max_tokens`，实测一次真实抽取里推理吃掉 19474 字、正文只剩 1418 字且断在半句，
    于是 12 个知识点的好答案变成 0 个、下游静默降级成研究计划。关掉之后 reasoning 为 0、
    `finish_reason` 由 `length` 转 `stop`。

    后半句（「只有这一次」）不是凑数：把开关加到**每一次**调用上会让 planner/evaluate
    也失去推理，那是另一件事，而这里不该悄悄发生。
    """
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, NODES_JSON])
    await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    assert llm.seen_extra_bodies == [None, None, THINKING_OFF] == [None, None, {"thinking": {"type": "disabled"}}]
    assert llm.seen_complete_max_tokens == [None, None, EXTRACT_MAX_TOKENS]


async def test_extract_salvages_the_nodes_out_of_a_truncated_reply():
    """正文断在半句时救回已完整的节点，而不是整份丢掉。

    夹具就是实测那次截断的形状：最后一个节点写到 `"key_points"` 中途就没了（少了收尾的
    `]}`）。`parse_json_object` 对它是 None——上一个版本因此交白卷、降级成研究计划。
    """
    truncated = NODES_JSON[: NODES_JSON.rindex('"key_points"')]
    assert parse_json_object(truncated) is None, "夹具必须先真的解析不出来，否则这条用例是空的"

    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, truncated])
    events = await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    nodes_event = next(e for e in events if isinstance(e, NodesEvent))
    assert [n["name"] for n in nodes_event.nodes] == ["文本切分"], "完整的那个节点必须被救回来"
    # 摘要写在数组**之后**，被截断时它必然一起丢——这不是缺陷，是 JSON 的顺序使然；
    # `learning` 层对空 summary 有兜底（`_lead(outline)`）。
    assert nodes_event.summary == ""
    assert events[-1].content == ""


async def test_extract_feeds_the_research_plan_and_the_evidence():
    """输入复用 synthesize 那一份（问题 + 计划 + 资料），只是最后一步换成输出 JSON。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, NODES_JSON])
    await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    user_content = llm.seen_messages[-1][1]["content"]
    assert "研究问题：RAG 怎么切分" in user_content
    assert "子问题A" in user_content


async def test_extract_without_evidence_says_so_instead_of_leaving_a_hole():
    """研究轮没采到证据（无工具调用）→ 抽取 prompt 里要有「暂无」，不能让模型猜。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, NODES_JSON])
    await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    assert "（暂无）" in llm.seen_messages[-1][1]["content"]


# ---- 抽不到时的行为：交白卷，不抛异常 -----------------------------------


async def test_garbage_reply_yields_no_nodes_and_a_status_message():
    """模型不吐 JSON → 空节点 + 一句状态提示。**不抛异常**：调用方据此降级到计划。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, "模型今天不想输出 JSON"])
    events = await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    nodes_event = next(e for e in events if isinstance(e, NodesEvent))
    assert nodes_event.nodes == [] and nodes_event.summary == ""
    assert any(
        isinstance(e, StatusEvent) and "没有抽取出知识点" in e.message for e in events
    )
    assert isinstance(events[-1], DoneEvent) and events[-1].content == ""


async def test_non_dict_nodes_are_filtered_out():
    """`[{"name": "A"}, null, "B"]`：留下能用的那个，别把整轮研究废掉。"""
    payload = json.dumps(
        {"nodes": [{"name": "文本切分"}, None, "B", 42], "summary": ""},
        ensure_ascii=False,
    )
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, payload])
    events = await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    nodes_event = next(e for e in events if isinstance(e, NodesEvent))
    assert [n["name"] for n in nodes_event.nodes] == ["文本切分"]


async def test_llm_failure_is_swallowed_into_an_empty_extraction():
    """抽取那一次挂了 → 当作「没抽到」。研究本身已经跑完了，不该因为最后一步炸掉。"""
    llm = _ExtractFails(script=SCRIPT_DIRECT)
    llm.complete_script = [PLANNER_JSON, EVAL_SUFFICIENT]
    events = await _run("RAG 怎么切分", llm, extract_prompt=LEARNING_PATH_PROMPT)

    assert llm.raised == 1, "抽取那一次没被触发——这条用例就白测了"
    nodes_event = next(e for e in events if isinstance(e, NodesEvent))
    assert nodes_event.nodes == []
    assert isinstance(events[-1], DoneEvent)


# ---- 默认参数：一个字都不变 ---------------------------------------------


async def test_without_the_prompt_the_terminal_node_is_still_synthesize():
    """`extract_prompt=None`（`/api/chat`、eval、loop 模式）走的是老路，逐字不变。"""
    llm = _fake([PLANNER_JSON, EVAL_SUFFICIENT, REPORT])
    events = await _run("研究问题", llm)

    assert not any(isinstance(e, NodesEvent) for e in events)
    assert events[-1].content == REPORT
    assert _extract_prompt_seen(llm) == SYNTHESIZE_PROMPT
    assert llm.seen_response_formats[-1] is None  # 报告不要求 JSON
