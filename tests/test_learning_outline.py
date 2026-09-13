"""Phase 10 走查反馈 ④：按需生成的「提纲」（`learning/outline.py`）。

这一层只测**纯函数**：把模型输出洗成条目（`clean_outline`）、从报告里挑片段
（`_excerpt`）、以及「任何失败都返回空」这条总闸门。落盘与端点行为在
`test_learning_store.py`（Markdown 层）与 `test_api_learning.py`（HTTP 层）里测。

**走查反馈 ② 之后多了一组「回退哨」**（文件末尾）：提纲的颗粒度是 prompt 里的一句
散文（「8~14 字的小点」），没有任何东西机械保证模型会照做——而下一版最容易被顺手
改掉的恰恰是这类散文。所以把「每条是小点」拆成几条可断言的字面契约钉住：字数区间
在 prompt 里、覆盖次序在 prompt 里、`ITEM_LIMIT` 贴着目标而不是宽松到没用。

这个模块的取舍与别处**相反**，所以测试的重点也相反：降级链（`build_graph_from_report`）
测的是「每一级都还有产出」，这里测的是「**任何异常都一个条目都不产出**」——因为提纲
会被永久写进用户自己的文件，而 `insert_outline` 从不覆写，写坏了没有第二次机会。
"""

import json

import pytest

from knowledge_pilot.learning import outline as outline_mod
from knowledge_pilot.learning.outline import (
    ITEM_LIMIT,
    MAX_ITEMS,
    clean_outline,
    generate_outline,
)

NODE = {
    "name": "文本切分",
    "type": "技术",
    "summary": "把长文档切成可检索的块",
    "key_points": ["定长切分", "递归切分"],
    "prerequisites": [{"name": "语料预处理"}],
}


def _reply(payload) -> str:
    return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


class _LLM:
    """最小假客户端：只记调用、返回预置文本（或抛预置异常）。"""

    def __init__(self, reply="", *, raises: BaseException | None = None) -> None:
        self.reply = reply
        self.raises = raises
        self.calls = 0
        self.prompts: list = []

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        self.calls += 1
        self.prompts.append(messages)
        if self.raises is not None:
            raise self.raises
        return self.reply


# ---- clean_outline：把模型输出洗成条目 -----------------------------------


def test_clean_outline_accepts_the_documented_shape():
    """条目长什么样——**走查反馈 ② 之后是 8~14 字的小点,不是一句话**。

    夹具跟着改成小点:它是这个模块「一份合法回复」的样本,写成一整句话的旧样本
    会让下一次读它的人以为长句是预期形状。
    """
    parsed = {"outline": ["有哪些分块方法", "各自的切分规则", "会遇到什么问题"]}
    assert clean_outline(parsed) == parsed["outline"]


def test_clean_outline_accepts_a_bare_array():
    """有的模型会把数组直接吐出来。意思一样，没必要为它烧一次重试。"""
    assert clean_outline(["一", "二", "三"]) == ["一", "二", "三"]


@pytest.mark.parametrize(
    "parsed",
    [None, "一段没有 JSON 的文字", {}, {"outline": "不是数组"}, {"outline": None},
     {"别的键": ["一"]}, 42, {"outline": [None, "", "   "]}, [[]]],
)
def test_clean_outline_returns_nothing_for_any_odd_shape(parsed):
    """形状不认识 → 空。**这里绝不猜**：猜错的代价是往用户文件里写进假提纲。"""
    assert clean_outline(parsed) == []


def test_clean_outline_normalizes_each_item():
    parsed = {"outline": [
        "1. 模型自己加的编号要剥掉",
        "- 项目符号也一样",
        "跨\n行的\n条目要折叠成一行",
        42,                      # 非字符串项照样能用
        "重复的条目",
        "重复的条目",             # 模型偶尔会给两遍
    ]}
    items = clean_outline(parsed)
    assert items[0] == "模型自己加的编号要剥掉"
    assert items[1] == "项目符号也一样"
    assert items[2] == "跨 行的 条目要折叠成一行"
    assert items[3] == "42"
    assert items.count("重复的条目") == 1


def test_clean_outline_truncates_instead_of_dropping():
    """超长条目截断而不是整条丢掉——丢一条就是从学习路径上挖掉一步。"""
    items = clean_outline({"outline": ["字" * 200, "短的"]})
    assert len(items) == 2
    assert items[0] == "字" * ITEM_LIMIT + "…"
    assert items[1] == "短的"


def test_clean_outline_caps_the_item_count():
    items = clean_outline({"outline": [f"第{i}步" for i in range(50)]})
    assert len(items) == MAX_ITEMS


# ---- _excerpt：从总报告里挑片段 ------------------------------------------


def test_excerpt_prefers_paragraphs_mentioning_the_node():
    report = "前言段落。\n\n" + ("无关段落。" * 500) + "\n\n文本切分的专门段落。\n\n尾巴。"
    out = outline_mod._excerpt(report, "文本切分", limit=80)
    assert "文本切分的专门段落。" in out
    assert len(out) <= 80


def test_excerpt_falls_back_to_the_head_when_nothing_matches():
    """报告按章节写、知识点名被模型改写，匹配不上是**正常**的——不能因此空手。"""
    report = "甲" * 500
    out = outline_mod._excerpt(report, "对不上的名字", limit=80)
    assert out == "甲" * 80


def test_excerpt_of_a_short_or_empty_report():
    assert outline_mod._excerpt("短报告", "X", limit=80) == "短报告"
    assert outline_mod._excerpt("", "X") == ""


# ---- generate_outline：永不抛，失败即空 ----------------------------------


async def test_generate_outline_happy_path_feeds_the_node_context():
    llm = _LLM(_reply({"outline": ["一", "二", "三"]}))
    items = await generate_outline(llm, NODE, topic_title="RAG", report="报告正文")
    assert items == ["一", "二", "三"]
    assert llm.calls == 1
    system, user = llm.prompts[0]
    assert system["role"] == "system" and user["role"] == "user"
    # 节点上下文真的进了 prompt（否则模型只能凭空编）
    assert "文本切分" in user["content"]
    assert "定长切分" in user["content"]
    assert "语料预处理" in user["content"]  # 前置知识点的名字
    assert "RAG" in user["content"]
    assert "报告正文" in user["content"]


async def test_generate_outline_parses_fenced_json():
    llm = _LLM("```json\n" + _reply({"outline": ["一", "二"]}) + "\n```")
    assert await generate_outline(llm, NODE) == ["一", "二"]


@pytest.mark.parametrize("reply", ["模型今天不想输出 JSON", "", "{}"])
async def test_generate_outline_returns_nothing_on_bad_output(reply):
    assert await generate_outline(_LLM(reply), NODE) == []


async def test_generate_outline_swallows_llm_failure_and_none_client():
    """模型挂了不该让整条请求 500——用户看到的应该是一次「可以重试」。"""
    assert await generate_outline(_LLM(raises=RuntimeError("503")), NODE) == []
    assert await generate_outline(None, NODE) == []


async def test_generate_outline_respects_max_items():
    llm = _LLM(_reply({"outline": [f"第{i}步" for i in range(20)]}))
    assert len(await generate_outline(llm, NODE, max_items=2)) == 2


# ---- 回退哨：提纲每一条必须是「小点」（走查反馈 ②）--------------------------
#
# 用户的原话是「这里的每一点都应该是列小点 不用很多的文字」。这不是排版问题，是
# prompt 问题：颗粒度只由那段散文决定，而**散文是最容易被顺手改掉的东西**。
# 下面几条把它拆成能机械检查的字面量。故意不测「模型真的照做了」——那要调模型，
# 既不稳定又慢；这里守的是**我们这一侧的契约还在**。


def test_prompt_asks_for_short_small_points_not_sentences():
    """长度区间与「名词短语」必须写在 prompt 里。

    删掉它，模型会回到第一版那种「说清分块粒度为什么会改变检索召回」的长句——
    而产物看起来仍然是一份合法提纲，`clean_outline` 一条都不会拦。
    """
    prompt = outline_mod.OUTLINE_PROMPT
    assert "8~14 字" in prompt, "颗粒度区间没了——模型没有依据写小点"
    assert "名词短语" in prompt, "没有说条目该是什么词性,模型会写句子"
    assert "不要把它拉长成一句话" in prompt, "少了对「写不下就拉长」这个失败模式的正面禁止"


def test_prompt_keeps_the_coverage_order():
    """覆盖次序（种类 → 做法 → 问题 → 处理）是用户点名要的四类。

    只断言「四个标记都在」不够：顺序才是语义——第 ① 条是「有哪些种类」还是
    「怎么调参」，读起来是两份完全不同的提纲。
    """
    prompt = outline_mod.OUTLINE_PROMPT
    marks = [prompt.index(m) for m in ("①", "②", "③", "④")]
    assert marks == sorted(marks), "覆盖次序被打乱了"
    assert "种类" in prompt and "怎么处理" in prompt, "四类里少了一类"


def test_prompt_still_demands_strict_json():
    """输出契约没变：`parse_json_object` + `clean_outline` 依赖它。

    改 prompt 里别的话时很容易把结尾那行示例一起覆盖掉——那时模型开始寒暄，
    整份提纲静默变成空（`generate_outline` 不抛）。
    """
    assert '{"outline": ["第一条", "第二条", "第三条"]}' in outline_mod.OUTLINE_PROMPT


def test_item_limit_hugs_the_target_instead_of_being_a_joke():
    """`ITEM_LIMIT` 是安全网，所以它得**贴着小点定**。

    80 字的上限形同虚设：一个 40 字的整句塞得进去，截断永远不会触发——于是
    「模型写长句」这件事在产物上完全看不出来。这里钉住上限不得放宽回那种程度，
    同时不得收紧到会砍掉合法小点的地步（8~14 字的目标要留出裕量）。
    """
    assert outline_mod.ITEM_LIMIT <= 40, "上限松到接不住整句——安全网失效"
    assert outline_mod.ITEM_LIMIT >= 20, "上限紧到会砍掉合法小点"


def test_a_whole_sentence_is_what_gets_cut():
    """安全网真的要能咬住一个整句（而不是只在 200 字时才触发）。"""
    sentence = "说清分块粒度为什么会改变检索召回以及它和重叠窗口之间的关系到底是什么"
    items = clean_outline({"outline": [sentence]})
    assert items and items[0].endswith("…")
    assert len(items[0]) <= outline_mod.ITEM_LIMIT + 1
