"""Agent Evaluation 指标：确定性 rubric / 工具精度 / 数值容差（纯 stdlib）。

口径说明（重要诚实边界）：
- 这些函数只判定**机制层**可确定判定的东西——报告覆盖词、工具选择/参数是否达到期望、
  数值容差。**报告质量本身**（相关性、深度、引用合理）离线不可判，交给 `--real` 的
  LLM judge（judge.py）；本模块不臆测质量。
- 词边界守卫：含 CJK 的 term 用子串命中（`len≥2`，单个汉字太弱）；纯拉丁/数字 term 用
  `\\bword\\b` 词边界（防 "RAG" 误命中 "RAGDOLL"）；空 term 视为已满足。
- 工具参数 `_loose_equal`：字符串归一化相等；数值相对容差 1e-6；任一侧 len≥8 的子串包含
  用于吸收真实模型的同义改写；**观测端多余键容忍**（模型多加 top_k 等不算错）。
- Token 成本 / 延迟统计复用 `rag.eval.metrics`（同一启发式，跨评测口径一致）。
"""

import math
import re
import unicodedata
from typing import Any, Iterable

from knowledge_pilot.rag.eval.metrics import est_tokens, latency_stats  # noqa: F401  再导出

# 中文 / 日文假名 / 韩文范围（用于「term 是否含 CJK → 用子串而非词边界」）。
_CJK_RE = re.compile(r"[　-〿぀-ヿ㐀-鿿豈-﫿가-힯]")


def normalize(text: str) -> str:
    """归一化：NFKC（全角→半角等）+ 去首尾空白 + 小写（中文不受影响）。"""
    return unicodedata.normalize("NFKC", text or "").strip().lower()


def term_hit(report_normalized: str, term: str) -> bool:
    """单个 must_include 词是否在（已归一化的）报告中命中。"""
    t = normalize(term)
    if not t:  # 空 term 视为已满足
        return True
    if len(t) < 2:  # 单词/单字太弱，防误报（"a" in "rag"、"图" 无处不在）
        return False
    if _CJK_RE.search(term):
        return t in report_normalized
    # 纯拉丁/数字 term → 词边界（"RAG" 不命中 "RAGDOLL"；"agentic RAG" 整串命中）。
    return re.search(rf"\b{re.escape(t)}\b", report_normalized) is not None


def coverage(must_include: Iterable[str], report: str) -> float:
    """报告覆盖度 = 命中词数 / 词总数；无 must_include → 1.0。"""
    terms = [t for t in must_include if isinstance(t, str)]
    if not terms:
        return 1.0
    report_norm = normalize(report)
    hits = sum(1 for t in terms if term_hit(report_norm, t))
    return hits / len(terms)


def passes_threshold(cov: float, threshold: float = 1.0) -> float:
    """coverage 是否达到阈值 → 1.0 / 0.0（用于 task_success 的确定性 rubric）。"""
    return 1.0 if cov >= threshold else 0.0


def tool_selection_accuracy(
    required_tools: Iterable[str], requested_tools: Iterable[str]
) -> float:
    """工具选择精度 = |required ∩ requested| / |required|。

    requested 来自 ToolCallEvent 的工具名集合（可含多余工具或未知工具——未知工具是否
    致死由 error_rate 判定，这里只查"该调的调了没"）；无 required → 1.0。
    """
    required = set(required_tools)
    if not required:
        return 1.0
    requested = set(requested_tools)
    return len(required & requested) / len(required)


def _loose_equal(a: Any, b: Any) -> bool:
    """宽松相等：字符串归一化相等；数值相对容差 1e-6；长度≥8 任一侧允许子串包含。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        try:
            return abs(a - b) <= 1e-6 * max(abs(a), abs(b), 1.0)
        except TypeError:
            return False
    if isinstance(a, str) and isinstance(b, str):
        na, nb = normalize(a), normalize(b)
        if na == nb:
            return True
        if len(na) >= 8 and na in nb:
            return True
        if len(nb) >= 8 and nb in na:
            return True
        return False
    return a == b  # 其余（含非字符串标量 / list 相等）按 Python 语义


def _gold_keys_satisfied(observed_args: dict[str, Any], gold_args: dict[str, Any]) -> int:
    """观测调用中满足的期望键数（键存在且 _loose_equal）。"""
    return sum(
        1
        for k, v in gold_args.items()
        if k in observed_args and _loose_equal(observed_args[k], v)
    )


def tool_argument_accuracy(
    expected_calls: Iterable["GoldToolCall"], observed_calls: Iterable[Any]
) -> float:
    """工具参数精度 = 满足的期望调用数 / 期望调用数。

    observed_calls 元素为 (name, arguments) 二元组或带 .name/.arguments 的对象；
    同名观测调用可重复（多轮搜索）。判定：每个期望调用在**未用过的**同名观测调用里
    贪心挑命中键最多的一个，全部期望键存在且 _loose_equal 才算满足；观测端多余键容忍。
    期望为空 → 1.0（无参数基准可判）。
    """
    expected = list(expected_calls)
    if not expected:
        return 1.0
    observed = [_as_call_pair(c) for c in observed_calls]
    used = [False] * len(observed)
    satisfied = 0
    for gold in expected:
        g_args = dict(getattr(gold, "arguments", {}))
        best_idx = -1
        best_keys = -1
        for i, (name, args) in enumerate(observed):
            if used[i] or name != gold.name:
                continue
            keys = _gold_keys_satisfied(args, g_args)
            if keys > best_keys:
                best_idx, best_keys = i, keys
        if best_idx >= 0 and best_keys == len(g_args):
            satisfied += 1
            used[best_idx] = True
    return satisfied / len(expected)


def _as_call_pair(c: Any) -> tuple[str, dict[str, Any]]:
    """统一观测调用的表示 → (name, arguments dict)。"""
    if isinstance(c, tuple) and len(c) == 2:
        return c[0], dict(c[1])
    return c.name, dict(getattr(c, "arguments", {}))


def mean(values: Iterable[float], default: float = 0.0) -> float:
    """算术平均；空输入返回 default（避免 runner 到处写空守卫）。"""
    vals = [v for v in values]
    if not vals:
        return default
    return sum(vals) / len(vals)


def est_tokens_from_message(content: str) -> int:
    """单条消息内容的 token 启发式（转发 rag/eval 口径，语义别名便于 offline 记账）。"""
    return max(1, math.ceil(len(content or "") / 2))
