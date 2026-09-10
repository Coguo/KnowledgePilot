"""Agent Evaluation judge A 轨测试（纯 stdlib，无需 langgraph）。

RubricJudge 确定性（覆盖/阈值/空词）；DeepSeekJudge 用脚本化 LLM（合法 JSON → aspects
生效、task_success 解析；不可解析 → 回退 rubric 不 raise；含 "json" 的 prompt 要求）。
"""

import json

from knowledge_pilot.agent.eval.dataset import AgentAspect, AgentItem
from knowledge_pilot.agent.eval.judge import DeepSeekJudge, RubricJudge
from knowledge_pilot.agent.eval.offline import ScriptedChatClient

GOOD_REPORT = "# 报告\n要点：\n- RAG\n- 知识图谱\n来源：…"


def _item(must_include, aspects=None) -> AgentItem:
    return AgentItem(
        query="q", must_include=tuple(must_include), aspects=tuple(aspects or [])
    )


async def test_rubric_judge_full_and_partial():
    judge = RubricJudge()
    item = _item(["RAG", "知识图谱"])
    v_full = await judge.score(item, GOOD_REPORT)
    assert v_full.task_success == 1.0
    assert v_full.source == "rubric"
    assert not judge.llm_based

    v_partial = await judge.score(item, "RAG 只做了检索与生成，回答完毕。")
    assert v_partial.task_success == 0.0
    assert "1/2" in v_partial.reason


async def test_rubric_judge_empty_terms_passes():
    judge = RubricJudge()
    v = await judge.score(_item([]), "随便什么报告")
    assert v.task_success == 1.0
    assert "无覆盖词" in v.reason


async def test_rubric_judge_custom_threshold():
    judge = RubricJudge(threshold=0.5)
    # 覆盖 1/2 的样本 → threshold 0.5 时算成功
    item = AgentItem(query="q", must_include=("词A", "词B"))
    v = await judge.score(item, "包含 词A 但没有 词B")
    assert v.task_success == 1.0


async def test_deepseek_judge_valid_json_and_aspects():
    aspects_raw = [
        AgentAspect(name="cites_sources", question="是否给出来源引用？"),
        AgentAspect(name="defines_both", question="是否定义了 GraphRAG 与 RAG？"),
    ]
    llm = ScriptedChatClient(
        [],
        complete_script=[
            json.dumps(
                {
                    "task_success": 1,
                    "aspects": {"cites_sources": True, "defines_both": False},
                    "reason": "引用齐全但区别说明不足",
                },
                ensure_ascii=False,
            )
        ],
    )
    judge = DeepSeekJudge(llm)
    assert judge.llm_based
    item = _item(["RAG"], aspects=aspects_raw)
    v = await judge.score(item, GOOD_REPORT)
    assert v.source == "llm"
    assert v.task_success == 1.0
    assert v.aspects == {"cites_sources": True, "defines_both": False}
    assert "引用齐全" in v.reason
    assert llm.complete_calls == 1
    # prompt 必须含单词 "json"（DeepSeek json_object 硬性要求）
    assert any("json" in (m.get("content") or "") for m in llm.seen_messages[0])


async def test_deepseek_judge_unparseable_falls_back_without_raise():
    llm = ScriptedChatClient([], complete_script=["这不是 JSON"])
    judge = DeepSeekJudge(llm)
    item = _item(["RAG", "知识图谱"])
    v = await judge.score(item, GOOD_REPORT)  # 不抛异常
    assert v.source == "rubric_fallback"
    assert v.task_success == 1.0  # 报告确实覆盖全部要点 → rubric 回退判成功
    assert "judge_fallback" in v.reason


async def test_deepseek_judge_llm_exception_falls_back():
    class _Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("网络炸了")

    judge = DeepSeekJudge(_Boom())
    item = _item(["RAG", "知识图谱"])
    v = await judge.score(item, GOOD_REPORT)
    assert v.source == "rubric_fallback"
    assert v.task_success == 1.0


async def test_deepseek_judge_no_aspects_keys():
    llm = ScriptedChatClient(
        [],
        complete_script=['{"task_success": 0, "aspects": {}, "reason": "没答到点上"}'],
    )
    judge = DeepSeekJudge(llm)
    item = _item([])  # 无 must_include / aspects
    v = await judge.score(item, "答非所问")
    assert v.source == "llm"
    assert v.task_success == 0.0
    assert v.aspects == {}
