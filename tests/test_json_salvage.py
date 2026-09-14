"""第七轮：`llm/json_utils.py::salvage_json` —— 从**被截断**的 JSON 里救回已完整的部分。

它存在的理由是一次实测故障：推理模型（`deepseek-flash`）的 `reasoning_content` 与正文
共用 `max_tokens`，一次真实抽取里推理吃掉 19474 字、正文只剩 1418 字且断在第 7 个节点的
半句（`…"可用LLM judge对难负例标注"]` 少了收尾的 `]}`）。`parse_json_object` 对它是
`None`，于是 12 个知识点的好答案变成 0 个、下游静默降级成研究计划。

这份用例的重点因此不是「解析器写得好不好」，而是三条契约：

1. **好输入一个字节都不动**（`salvage_json` 是 `parse_json_object` 的超集，不是替代品）——
   它被塞进了两个原本只用 `parse_json_object` 的调用点，回归的代价必须是零；
2. **截断时交回完整的那部分**，而不是整份丢掉，也不是半句话；
3. **救不回来时仍然是 `None`**（调用方据此走它原来那条「什么都没有」的路）。

每个截断用例都**同时**断言 `parse_json_object` 的表现。这不是冗长的重复：那些差异
（它会捡回一个内层数组、或裸数组里的第一个元素）正是 `salvage_json` 绕开的坑，
把它们写下来，下一次改这个文件的人才不会以为早退那一档可以简化成「不是 None 就返回」。
"""

import json

import pytest

from knowledge_pilot.llm.json_utils import parse_json_object, salvage_json


def _dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ---- 契约 1：好输入一个字节都不动 -----------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        '{"sufficient": true, "reason": "资料足够", "gap": ""}',
        '{"nodes": [{"name": "文本切分", "key_points": ["定长", "递归"]}], "summary": "s"}',
        '{"outline": ["有哪些分块方法", "各自的切分规则"]}',
        '{"outline": [1, 2, 3]}',                       # 数组里不是对象
        '[{"name": "A"}, {"name": "B"}]',               # 裸数组（完整）
        '```json\n{"outline": ["一", "二"]}\n```',       # 代码围栏
        '好的，结果如下：\n{"outline": ["一", "二"]}',     # 前缀废话
        "模型今天不想输出 JSON",                          # 垃圾
        "",
    ],
)
def test_a_complete_answer_goes_through_unchanged(text):
    """`salvage_json` 在**没有截断**的输入上必须与 `parse_json_object` 完全一致。

    它替换了两个调用点上原本的 `parse_json_object`。任何「顺手多救一点」的行为都会
    让那两处的既有语义（尤其是「拿不准就倒向空」）悄悄变松 —— 所以这条不是形式主义，
    它是「替换是安全的」这个结论本身。
    """
    assert salvage_json(text) == parse_json_object(text)


def test_none_and_whitespace_are_still_none():
    """两个输入形状不经过 `parse_json_object` 的字符串路径，单独钉一下。"""
    assert salvage_json(None) is None
    assert salvage_json("   \n  ") is None


# ---- 契约 2：截断时交回完整的那部分 ---------------------------------------

def test_truncated_outer_object_keeps_the_complete_elements():
    """**这一条就是实测那次故障的形状**：数组闭合了、外层的 `}` 没跟上（少了 `]}` 的后半个）。

    这种输入下 `parse_json_object` 会捡回那个**内层数组**并把它当成整份结果 ——
    它不是 `None`，但对调用方毫无意义（`extract_node` 要的是 `{"nodes": …}`）。
    这正是「parsed 不是 None 就早退」会漏掉的那一档。
    """
    truncated = ('{"nodes": ['
                 '{"name": "文本切分", "key_points": ["定长"]}, '
                 '{"name": "向量检索", "key_points": ["embedding"]}]')
    # 前提：这份夹具真的少了收尾，否则下面两句断言是空的。
    assert not truncated.endswith("]}")
    assert isinstance(parse_json_object(truncated), list), \
        "parse 把 nodes 数组当成了整份结果——这正是要绕开的坑"

    got = salvage_json(truncated)
    assert isinstance(got, dict)
    assert [n["name"] for n in got["nodes"]] == ["文本切分", "向量检索"]
    # 救回来的是**完整节点**，不是一个「名字有、要点缺」的空壳。
    assert got["nodes"][1]["key_points"] == ["embedding"]


def test_truncation_inside_a_string_drops_that_element_not_the_whole_thing():
    """断在**字符串中间**：那一条整个丢掉，前面完整的留下（不留半句话）。

    这种形状（最后一个元素自己都没写完）`parse_json_object` 交的是 `None` ——
    两条路径都配不平括号。所以救不救得回来完全取决于 `salvage_json`。
    """
    truncated = '{"nodes": [{"name": "A", "key_points": ["x"]}, {"name": "B", "key_points": ["y'
    assert parse_json_object(truncated) is None

    got = salvage_json(truncated)
    assert got == {"nodes": [{"name": "A", "key_points": ["x"]}]}
    assert '"B"' not in _dump(got), "截断那一条被当成了完整节点写了进去"


def test_truncation_inside_a_nested_array_keeps_the_earlier_arrays():
    """截断发生在**内层数组**里：救回的节点保留它自己那个数组的完整元素。"""
    truncated = '{"nodes": [{"name": "A", "key_points": ["p", "q", "r'
    got = salvage_json(truncated)
    assert got == {"nodes": [{"name": "A", "key_points": ["p", "q"]}]}


def test_the_outline_shape_is_salvageable():
    """`{"outline": ["甲", "乙", "丙` —— **一个括号都没闭合**，只认括号就整份丢。

    而它恰恰是「数组装字符串」的必然截断形态（提纲就是这个形状），所以
    `_checkpoints` 把「数组里的字符串闭合」也算作边界。提纲那一路因此能救回前两条。
    """
    assert parse_json_object('{"outline": ["甲", "乙", "丙') is None
    assert salvage_json('{"outline": ["甲", "乙", "丙') == {"outline": ["甲", "乙"]}


def test_the_earliest_checkpoint_wins_not_the_first_one_that_parses():
    """救的是**最长**的那个能解析的前缀，不是碰巧第一个能解析的。

    `_salvage_from` 里遍历的是 `reversed(checkpoints)`；写成正序的话第一个闭合点
    （往往是内层数组的 `]`）会先被接受，于是一份 3 个节点的回复只交回 1 个 ——
    而且它**看起来完全正常**，没有任何东西会喊。
    """
    text = _dump({"nodes": [{"name": "A", "key_points": ["x"]},
                            {"name": "B", "key_points": ["y"]}]})
    truncated = text[: text.rindex('"y"')]
    got = salvage_json(truncated)
    assert isinstance(got, dict)
    assert [n["name"] for n in got["nodes"]] == ["A"]


def test_a_fenced_truncated_reply_is_salvaged_inside_the_fence():
    """围栏与截断同时发生：围栏摘掉之后的那段才是要修的东西。"""
    truncated = '```json\n{"nodes": [{"name": "A", "key_points": ["x"]}, {"name": "B", "ke'
    assert salvage_json(truncated) == {"nodes": [{"name": "A", "key_points": ["x"]}]}


def test_prose_before_a_truncated_object_is_skipped():
    """前缀废话（「好的，结果如下：」）+ 截断：从**文本里最早的**那个括号起算。"""
    truncated = '好的，结果如下：\n{"nodes": [{"name": "A", "key_points": ["x"]}, {"name": "B'
    assert salvage_json(truncated) == {"nodes": [{"name": "A", "key_points": ["x"]}]}


def test_a_truncated_bare_array_keeps_its_complete_elements():
    """裸数组截断：`parse_json_object` 会捡回**第一个元素**顶替整份结果（坑之二）。

    `salvage_json` 必须交回**数组**（两个元素），因为下游拿到的是一份列表：
    一个 dict 混进列表处理路径，轻则少一个节点，重则 `AttributeError`。
    """
    truncated = '[{"name": "A", "key_points": ["x"]}, {"name": "B", "key_points": ["y'
    parsed = parse_json_object(truncated)
    assert isinstance(parsed, dict), "parse 把第一个元素当成了整份结果——这正是要绕开的坑"

    got = salvage_json(truncated)
    assert isinstance(got, list)
    assert [n["name"] for n in got] == ["A"]


def test_a_complete_bare_array_is_not_reduced_to_its_first_element():
    """完整裸数组**不许**被切成第一个元素（那是上一稿里真实踩到的回归）。

    成因：为了容忍前缀废话而「从文本里第一个 `{` 起切」，而裸数组第一个字符是 `[`、
    第 1 位就是 `{` —— 切下去正好只剩元素一。
    """
    text = '[{"name": "A"}, {"name": "B"}]'
    assert salvage_json(text) == parse_json_object(text) == [{"name": "A"}, {"name": "B"}]


def test_a_dict_that_is_an_array_element_is_never_passed_off_as_the_whole_thing():
    """数组元素形状的对象**不冒充**整份结果（`raw[:1] != "["` 那个条件守的就是它）。"""
    assert salvage_json('[{"nodes": [{"name": "A"}]}]') == [{"nodes": [{"name": "A"}]}]


# ---- 契约 3：救不回来时仍然是 None ---------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        '{"nodes": [{"name": "A',          # 元素还没写完，没有任何完整元素
        '{"outline": ["甲',                 # 提纲第一条就断
        '[{"name": "A',                    # 裸数组的第一个元素就断
        '{"nodes": [',                     # 数组开了个头就没有了
        "模型今天不想输出 JSON",
        "",
    ],
)
def test_nothing_salvageable_is_still_none(text):
    """没有完整元素可救时交回 `None` —— 与 `parse_json_object` 的失败语义一致。

    这一条的重要性在于它**不是**「返回一个空壳」：调用方（`extract_node`、
    `generate_outline`）对 `None` 走的是它们原来那条「这次什么都没有」的路 ——
    抽不到就降级、提纲就不落盘。返回 `{"nodes": []}` 之类会让那条路看起来「成功」，
    于是降级不再发生、重试不再提示，坏的是一整条静默链路。
    """
    assert salvage_json(text) is None


def test_partial_repair_never_invents_a_key():
    """救回来的部分**只含原文里有的键** —— 修补的是括号，不是内容。"""
    got = salvage_json('{"nodes": [{"name": "A", "key_points": ["x"]}, {"name": "B')
    assert got == {"nodes": [{"name": "A", "key_points": ["x"]}]}
    assert set(got) == {"nodes"}
    assert set(got["nodes"][0]) == {"name", "key_points"}
