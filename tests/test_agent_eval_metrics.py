"""Agent Evaluation 确定性指标 A 轨测试（纯 stdlib，无需 langgraph）。

覆盖 rubric 词边界守卫 / CJK 子串 / len≥2、覆盖度阈值、工具选择精度、
工具参数精度（贪心对齐 / 多余键容忍 / 重复调用 / 数值容差 / 长串子串）。
"""

from knowledge_pilot.agent.eval.dataset import GoldToolCall
from knowledge_pilot.agent.eval.metrics import (
    _loose_equal,
    coverage,
    normalize,
    passes_threshold,
    term_hit,
    tool_argument_accuracy,
    tool_selection_accuracy,
)


# ---- normalize / term_hit（词边界守卫）------------------------------------


def test_normalize_nfkc_and_case():
    assert normalize("  ＧraphＲAG  ") == "graphrag"
    assert normalize("知识图谱\n") == "知识图谱"


def test_term_latin_word_boundary_guards():
    # "RAG" 不命中 "RAGDOLL"（词边界守卫），但命中独立词 "RAG"
    assert term_hit(normalize("GraphRAG 与 RAGDOLL 对比"), "RAG") is False
    assert term_hit(normalize("经典 RAG 与 GraphRAG"), "RAG") is True


def test_term_cjk_substring_matches():
    assert term_hit(normalize("用知识图谱缓解幻觉"), "知识图谱") is True
    assert term_hit(normalize("没有这个词"), "知识图谱") is False


def test_term_short_single_char_never_hits():
    # len<2 守卫：单中文字"图"太弱、单词"a"太弱 → 一律不满足
    assert term_hit(normalize("图神经网络里有很多图"), "图") is False
    assert term_hit(normalize("a is here a"), "a") is False


def test_term_empty_treated_satisfied():
    assert term_hit(normalize("anything"), "") is True


def test_term_mixed_latin_cjk_space_phrase():
    assert term_hit(normalize("本次采用 RAG 优化 与分层检索"), "RAG 优化") is True
    assert term_hit(normalize("RAG优化"), "RAG 优化") is False  # 词间空格差异 → 未命中


# ---- coverage / passes_threshold ------------------------------------------


def test_coverage_ratio_and_empty():
    assert coverage(["GraphRAG", "RAG", "知识图谱"], "关于 GraphRAG、RAG 与知识图谱的报告") == 1.0
    assert coverage(["RAG", "幻觉"], "只有 RAG") == 0.5
    assert coverage([], "随便") == 1.0


def test_coverage_cjk_word_boundary():
    # 纯拉丁词命中有词边界；"RAGDOLL" 不含独立 "RAG"
    assert coverage(["RAG"], "GraphRAG 是 RAGDOLL 的变体") == 0.0
    assert coverage(["RAG"], "经典 RAG 流程") == 1.0


def test_passes_threshold():
    assert passes_threshold(0.5) == 0.0  # 默认阈值 1.0
    assert passes_threshold(1.0) == 1.0
    assert passes_threshold(0.5, threshold=0.5) == 1.0
    assert passes_threshold(0.6, threshold=0.5) == 1.0


# ---- tool selection -------------------------------------------------------


def test_selection_empty_required_is_one():
    assert tool_selection_accuracy([], ["search_web"]) == 1.0


def test_selection_all_required_called():
    assert tool_selection_accuracy(["search_web"], ["search_web", "search_papers"]) == 1.0


def test_selection_missing_and_extra():
    assert tool_selection_accuracy(["search_web"], ["search_papers"]) == 0.0
    assert tool_selection_accuracy(["search_web", "search_papers"], ["search_web"]) == 0.5
    # 未知工具也算 selected（崩不崩由 error_rate 判定），不拉低选择精度
    assert tool_selection_accuracy(["search_web"], ["mystery_tool", "search_web"]) == 1.0


# ---- loose_equal ----------------------------------------------------------


def test_loose_equal_numbers_relative_tolerance():
    assert _loose_equal(5, 5)
    assert _loose_equal(5.000001, 5.0)
    assert not _loose_equal(5.1, 5.0)
    assert not _loose_equal(5, "5")  # 类型不同不相等


def test_loose_equal_strings_normalized():
    assert _loose_equal("  GraphRAG  ", "graphrag")
    assert _loose_equal("ＡＲＡＧ", "ARAG")  # NFKC 全角→半角


def test_loose_equal_long_string_substring():
    # 任一侧 len≥8 允许子串包含（吸收 DeepSeek 改写）
    assert _loose_equal("一个足够长的查询改写测试句子", "一个足够长的查询改写测试句子用于搜索")
    assert _loose_equal("short", "short")
    assert not _loose_equal("ab", "abcdef")  # 双侧 <8 → 严格相等


# ---- tool argument accuracy -----------------------------------------------


def test_argument_empty_expectations_is_one():
    assert tool_argument_accuracy([], [("search_web", {"query": "x"})]) == 1.0


def test_argument_exact_and_extra_keys_tolerated():
    exp = [GoldToolCall(name="search_web", arguments={"query": "GraphRAG 与 RAG 的区别"})]
    obs = [("search_web", {"query": "GraphRAG 与 RAG 的区别", "max_results": 5})]
    assert tool_argument_accuracy(exp, obs) == 1.0


def test_argument_greedy_align_repeated_calls():
    # 两个期望调用 + 顺序颠倒的观测调用 → 各自贪心对齐，全满足
    exp = [
        GoldToolCall(name="search_web", arguments={"query": "查询一", "top_k": 3}),
        GoldToolCall(name="search_web", arguments={"query": "查询二"}),
    ]
    obs = [
        ("search_web", {"query": "查询二"}),
        ("search_web", {"query": "查询一", "top_k": 3}),
    ]
    assert tool_argument_accuracy(exp, obs) == 1.0


def test_argument_duplicate_gold_single_observed():
    # 一个观测调用只能被一个期望调用匹配 → 0.5
    exp = [
        GoldToolCall(name="search_web", arguments={"query": "同一个查询"}),
        GoldToolCall(name="search_web", arguments={"query": "同一个查询"}),
    ]
    obs = [("search_web", {"query": "同一个查询"})]
    assert tool_argument_accuracy(exp, obs) == 0.5


def test_argument_wrong_value_fails():
    exp = [GoldToolCall(name="search_web", arguments={"query": "期望查询"})]
    obs = [("search_web", {"query": "完全不同的查询"})]
    assert tool_argument_accuracy(exp, obs) == 0.0


def test_argument_numeric_loose():
    exp = [GoldToolCall(name="search_papers", arguments={"max_results": 5})]
    assert tool_argument_accuracy(exp, [("search_papers", {"max_results": 5.0000005})]) == 1.0


def test_argument_wrong_tool_name_ignored():
    exp = [GoldToolCall(name="search_papers", arguments={"query": "arxiv"})]
    obs = [("search_web", {"query": "arxiv"})]
    assert tool_argument_accuracy(exp, obs) == 0.0


def test_argument_object_shaped_observed():
    # observed 也支持带 .name/.arguments 的对象（ToolCallEvent 形状）
    class _E:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    exp = [GoldToolCall(name="search_web", arguments={"query": "q"})]
    assert tool_argument_accuracy(exp, [_E("search_web", {"query": "q"})]) == 1.0
