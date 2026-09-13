"""Phase 9 M4：学习路径的抽取与**确定性**图构建。

这一层刻意不碰网络也不碰数据库：`build_learning_graph` 是纯函数，LLM 的脏输出
（同名、重复、环、自相矛盾的 order）全部在这里被确定性地清洗掉。把「模型质量」
和「产品可用性」解耦的关键就在这——模型抽得烂，用户拿到的是一条线性路径，而不是
一个崩溃或一张空图。
"""

import json

import pytest

from knowledge_pilot.learning.path import (
    build_learning_graph,
    extract_learning_nodes,
    nodes_from_headings,
    nodes_from_plan,
)


def _node(name, *, order=None, prerequisites=(), key_points=(), **extra):
    return {
        "name": name,
        "order": order,
        "prerequisites": list(prerequisites),
        "key_points": list(key_points),
        **extra,
    }


def _by_name(graph):
    return {n["name"]: n for n in graph["nodes"]}


# ---- build_learning_graph：结构 ---------------------------------------


def test_empty_input_yields_empty_graph():
    assert build_learning_graph([]) == {"nodes": [], "edges": []}
    assert build_learning_graph(None) == {"nodes": [], "edges": []}


def test_linear_chain_gets_increasing_depth_and_order():
    graph = build_learning_graph(
        [
            _node("文本切分", order=1),
            _node("向量检索", order=2, prerequisites=["文本切分"]),
            _node("重排序", order=3, prerequisites=["向量检索"]),
        ]
    )
    nodes = _by_name(graph)
    assert [nodes[n]["depth"] for n in ("文本切分", "向量检索", "重排序")] == [0, 1, 2]
    assert [nodes[n]["order_index"] for n in ("文本切分", "向量检索", "重排序")] == [0, 1, 2]
    assert graph["edges"] == [
        {"source": "文本切分", "target": "向量检索", "relation": "前置"},
        {"source": "向量检索", "target": "重排序", "relation": "前置"},
    ]


def test_diamond_puts_shared_prerequisite_first():
    graph = build_learning_graph(
        [
            _node("基础", order=1),
            _node("左", order=2, prerequisites=["基础"]),
            _node("右", order=3, prerequisites=["基础"]),
            _node("合流", order=4, prerequisites=["左", "右"]),
        ]
    )
    nodes = _by_name(graph)
    assert nodes["基础"]["depth"] == 0
    assert nodes["左"]["depth"] == nodes["右"]["depth"] == 1
    assert nodes["合流"]["depth"] == 2


def test_topological_order_beats_llm_order():
    """前置一致性优先于模型的主观顺序。

    LLM 说「高级」排第 1、它依赖的「基础」排第 5——若照抄 order 就会出现
    「箭头指向 order_index 更大的节点」的视觉矛盾。所以 order_index 用拓扑序。
    """
    graph = build_learning_graph(
        [
            _node("高级", order=1, prerequisites=["基础"]),
            _node("基础", order=5),
        ]
    )
    nodes = _by_name(graph)
    assert nodes["基础"]["order_index"] == 0
    assert nodes["高级"]["order_index"] == 1


def test_llm_order_breaks_ties_among_independent_nodes():
    """同层平局时才用 LLM 的 order——它代表模型对并列项的偏好，值得尊重。"""
    graph = build_learning_graph(
        [
            _node("丙", order=3),
            _node("甲", order=1),
            _node("乙", order=2),
        ]
    )
    assert [n["name"] for n in graph["nodes"]] == ["甲", "乙", "丙"]


# ---- build_learning_graph：清洗 ---------------------------------------


def test_duplicate_names_merge_and_union_their_points():
    graph = build_learning_graph(
        [
            _node("向量检索", order=1, key_points=["相似度"]),
            _node("向量检索", order=2, key_points=["相似度", "索引结构"]),
        ]
    )
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["key_points"] == ["相似度", "索引结构"]  # 并集 + 保序去重


def test_max_nodes_truncates_by_llm_order():
    graph = build_learning_graph(
        [_node(f"知识{i}", order=i) for i in range(1, 21)], max_nodes=3
    )
    assert [n["name"] for n in graph["nodes"]] == ["知识1", "知识2", "知识3"]


def test_prerequisites_pointing_at_unknown_names_are_dropped():
    """模型幻觉出来的依赖不该变成悬空边（图里没有那个节点）。"""
    graph = build_learning_graph(
        [_node("A", order=1, prerequisites=["不存在的知识点"]), _node("B", order=2)]
    )
    assert graph["edges"] == []
    assert _by_name(graph)["A"]["prerequisites"] == []


def test_self_prerequisite_is_dropped():
    graph = build_learning_graph([_node("A", order=1, prerequisites=["A"])])
    assert graph["edges"] == []
    assert _by_name(graph)["A"]["depth"] == 0


def test_max_nodes_never_drops_below_one():
    graph = build_learning_graph([_node("A")], max_nodes=0)
    assert len(graph["nodes"]) == 1


def test_non_dict_items_are_dropped_instead_of_crashing():
    """模型偶尔往数组里塞一个 null / 字符串；那时整张图不能连锅端掉。

    这条是第六轮补的：`build_graph_from_material` 的契约是「无论喂进来什么都返回一张图」，
    而 `node.get("name")` 遇到 `None` 是 `AttributeError` —— 它会把**已经清洗好的**
    那几个节点一起炸掉（测试里先是直接炸出 `AttributeError` 才发现）。
    """
    graph = build_learning_graph([{"name": "A"}, None, "B", 42, {"name": "C"}])
    assert [n["name"] for n in graph["nodes"]] == ["A", "C"]


def test_numbering_prefix_is_stripped_from_names():
    graph = build_learning_graph(
        [_node("1. 文本切分", order=1), _node("二、向量检索", order=2), _node("（3）重排序", order=3)]
    )
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索", "重排序"]


def test_nodes_without_order_keep_input_sequence():
    graph = build_learning_graph([_node("A"), _node("B"), _node("C")])
    assert [n["name"] for n in graph["nodes"]] == ["A", "B", "C"]


def test_unnamed_nodes_are_dropped():
    graph = build_learning_graph([_node("  "), _node(None), _node("有效")])
    assert [n["name"] for n in graph["nodes"]] == ["有效"]


# ---- build_learning_graph：环与深链 -----------------------------------


def test_two_node_cycle_is_broken_without_hanging():
    """A 依赖 B、B 依赖 A 是真实会出现的（模型自相矛盾），必须打断而不是死循环。"""
    graph = build_learning_graph(
        [_node("A", order=1, prerequisites=["B"]), _node("B", order=2, prerequisites=["A"])]
    )
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 2  # 两条边都保留，只是顺序被打断


def test_long_cycle_is_broken_and_every_node_gets_a_depth():
    names = [f"K{i}" for i in range(6)]
    graph = build_learning_graph(
        [_node(n, order=i, prerequisites=[names[(i - 1) % len(names)]])
         for i, n in enumerate(names)]
    )
    assert len(graph["nodes"]) == len(names)
    assert all(isinstance(n["depth"], int) for n in graph["nodes"])
    # 破环后仍是一条链：depth 从 0 递增到 n-1
    assert sorted(n["depth"] for n in graph["nodes"]) == list(range(len(names)))


def test_deep_chain_does_not_hit_recursion_limit():
    """迭代算 depth 的理由：500 级前置链用递归会 RecursionError。"""
    nodes = [_node("K0", order=0)]
    nodes += [_node(f"K{i}", order=i, prerequisites=[f"K{i - 1}"]) for i in range(1, 500)]
    graph = build_learning_graph(nodes, max_nodes=500)
    assert len(graph["nodes"]) == 500
    assert _by_name(graph)["K499"]["depth"] == 499


# ---- 降级链：nodes_from_headings --------------------------------------


def test_headings_become_a_linear_path():
    report = "## 文本切分\n\n正文\n\n## 向量检索\n\n正文\n\n## 重排序\n"
    graph = build_learning_graph(nodes_from_headings(report))
    nodes = _by_name(graph)
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索", "重排序"]
    assert nodes["文本切分"]["depth"] == 0
    assert nodes["重排序"]["depth"] == 2


def test_structural_headings_are_filtered_out():
    report = "# 总标题\n\n## 摘要\n\n## 文本切分\n\n## 结论\n\n## 参考文献\n"
    names = [n["name"] for n in nodes_from_headings(report)]
    assert names == ["文本切分"]


def test_falls_back_to_h3_when_h2_is_scarce():
    report = "## 唯一的章节\n\n### 小节一\n\n### 小节二\n\n### 小节三\n"
    names = [n["name"] for n in nodes_from_headings(report)]
    assert names == ["唯一的章节", "小节一", "小节二", "小节三"]


def test_headings_keep_input_order_and_dedup():
    report = "## B\n\n## A\n\n## B\n"
    assert [n["name"] for n in nodes_from_headings(report)] == ["B", "A"]


def test_report_without_headings_yields_one_node():
    """连标题都没有时给一个单节点——用户至少能开始学，而不是面对空图。"""
    nodes = nodes_from_headings("一段没有任何标题的普通文字。", fallback_name="RAG 入门")
    assert [n["name"] for n in nodes] == ["RAG 入门"]
    assert nodes[0]["prerequisites"] == []


def test_report_without_headings_and_without_name_still_yields_a_node():
    assert [n["name"] for n in nodes_from_headings("")] == ["本主题"]


def test_h1_is_not_treated_as_a_knowledge_point():
    """`#` 是整篇报告的标题，不是知识点；只有 `##`/`###` 参与降级解析。"""
    assert [n["name"] for n in nodes_from_headings("# 报告标题\n\n正文\n")] == ["本主题"]


# ---- 降级链：nodes_from_plan ------------------------------------------


PLAN = [
    {"title": "文本切分", "question": "怎么切", "purpose": "打基础"},
    {"title": "向量检索", "question": "怎么检索", "purpose": "接着学"},
]


def test_plan_becomes_a_linear_path():
    """计划是这条路上**一定存在**的东西（planner 先跑），所以它是降级的第二级。"""
    graph = build_learning_graph(nodes_from_plan(PLAN))
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索"]
    nodes = _by_name(graph)
    assert nodes["文本切分"]["depth"] == 0 and nodes["向量检索"]["depth"] == 1
    assert nodes["向量检索"]["prerequisites"] == ["文本切分"]


def test_plan_nodes_carry_no_keywords():
    """计划里的句子是**研究问题**，不是「要掌握的关键词」——塞进关键词芯片就是编造。"""
    nodes = nodes_from_plan(PLAN)
    assert all(n["key_points"] == [] for n in nodes)
    assert nodes[0]["type"] == "章节"  # 与 nodes_from_headings 同一个词：结构性，非抽取
    assert nodes[0]["summary"] == "打基础"


def test_plan_falls_back_to_the_question_when_title_is_missing():
    nodes = nodes_from_plan([{"question": "什么是重排序？", "purpose": "最后一步"}])
    assert [n["name"] for n in nodes] == ["什么是重排序？"]


def test_plan_dedups_and_skips_blank_titles():
    plan = [{"title": "A"}, {"title": "A"}, {"title": "  "}, {"title": "B"}]
    assert [n["name"] for n in nodes_from_plan(plan)] == ["A", "B"]


def test_empty_plan_yields_nothing_instead_of_a_single_node():
    """**刻意没有单节点兜底**：一个只有一个点的图正是用户报的那个故障的样子。"""
    assert nodes_from_plan([]) == []
    assert nodes_from_plan(None) == []
    assert nodes_from_plan([{"title": ""}]) == []


def test_plan_survives_junk_steps():
    """计划项来自模型/checkpoint，什么都可能：非 dict、数字、缺字段。降级路径不许抛异常。"""
    assert [n["name"] for n in nodes_from_plan(["字符串", 42, None, {}])] == []
    assert [n["name"] for n in nodes_from_plan([{"title": "编号"}, 42])] == ["编号"]


# ---- 降级链：extract_learning_nodes -----------------------------------


class _FakeLLM:
    def __init__(self, reply=None, *, raises=None):
        self.reply = reply
        self.raises = raises
        self.calls = 0
        self.saw_response_format = None

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        self.calls += 1
        self.saw_response_format = response_format
        if self.raises:
            raise self.raises
        return self.reply


async def test_extract_parses_valid_json():
    llm = _FakeLLM('{"nodes": [{"name": "文本切分", "prerequisites": [], "order": 1}]}')
    nodes = await extract_learning_nodes(llm, "一些资料")
    assert [n["name"] for n in nodes] == ["文本切分"]
    # DeepSeek 的 json_object 模式要求 prompt 里出现字面量 "json"
    assert llm.saw_response_format == {"type": "json_object"}


async def test_extract_requires_json_word_in_prompt():
    from knowledge_pilot.learning.path import LEARNING_PATH_PROMPT

    assert "json" in LEARNING_PATH_PROMPT.lower()


async def test_extract_returns_empty_on_llm_failure():
    """任何失败都返回 []（由调用方降级），绝不把异常抛给用户。"""
    assert await extract_learning_nodes(_FakeLLM(raises=RuntimeError("boom")), "资料") == []


async def test_extract_returns_empty_on_non_json_reply():
    assert await extract_learning_nodes(_FakeLLM("我不知道该怎么回答"), "资料") == []


async def test_extract_returns_empty_when_nodes_key_is_missing_or_wrong_type():
    assert await extract_learning_nodes(_FakeLLM('{"foo": 1}'), "资料") == []
    assert await extract_learning_nodes(_FakeLLM('{"nodes": "不是数组"}'), "资料") == []


async def test_extract_skips_empty_text_without_calling_llm():
    llm = _FakeLLM('{"nodes": []}')
    assert await extract_learning_nodes(llm, "   ") == []
    assert llm.calls == 0


async def test_extract_accepts_fenced_json():
    llm = _FakeLLM('```json\n{"nodes": [{"name": "A"}]}\n```')
    assert [n["name"] for n in await extract_learning_nodes(llm, "资料")] == ["A"]


@pytest.mark.parametrize("bad", [None, 123, "文本", [], {}, 3.5, True])
async def test_extract_survives_wrong_typed_fields(bad):
    """`key_points` / `prerequisites` 不是数组时不能崩——模型常把单元素写成标量。

    注意这些是**合法 JSON**（用 json.dumps 构造）：非法 JSON 该走的是「降级」那条路，
    由 test_extract_returns_empty_on_non_json_reply 覆盖，两者不能混为一谈。
    """
    llm = _FakeLLM(json.dumps({"nodes": [{"name": "A", "key_points": bad,
                                         "prerequisites": bad}]}))
    nodes = await extract_learning_nodes(llm, "资料")
    assert [n["name"] for n in nodes] == ["A"]
    assert isinstance(nodes[0]["key_points"], list)


async def test_extract_wraps_scalar_single_item_fields():
    """只有一个前置时模型常写 `"prerequisites": "基础"`——这应被当成单元素列表。"""
    llm = _FakeLLM(json.dumps({"nodes": [
        {"name": "基础", "prerequisites": ""},
        {"name": "进阶", "prerequisites": "基础"},
    ]}))
    graph = build_learning_graph(await extract_learning_nodes(llm, "资料"))
    assert graph["edges"] == [{"source": "基础", "target": "进阶", "relation": "前置"}]
