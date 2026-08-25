"""检索侧评测指标：Recall@K、MRR、Latency、Token Cost。

- 相关判定在 document 级：同一文档多个 chunk 命中只算一次。
- Token Cost 用启发式 `est_tokens = max(1, ceil(len/2))`（约 2 字符/token，
  中英混合文本的粗略估算），只用于**相对比较**（哪个方案更省 token），
  不用于精确计费。
"""

import math
from statistics import mean
from typing import Iterable


def est_tokens(text: str) -> int:
    """粗略 token 估算：约 2 字符/token，下限 1。"""
    return max(1, math.ceil(len(text) / 2))


def recall_at_k(
    hit_doc_ids: list[str], relevant_doc_ids: Iterable[str], k: int
) -> float:
    """前 K 个检索结果中命中的相关文档数 / 相关文档总数（document 级去重）。

    检索结果里同一文档的多个 chunk 只算一次；K ≤ 0 或相关集为空 → 0。
    """
    relevant = set(relevant_doc_ids)
    if not relevant or k <= 0:
        return 0.0
    hit = relevant.intersection(hit_doc_ids[:k])
    return len(hit) / len(relevant)


def mrr(hit_doc_ids: list[str], relevant_doc_ids: Iterable[str]) -> float:
    """Mean Reciprocal Rank：首个相关文档的倒数排名；无命中返回 0。"""
    relevant = set(relevant_doc_ids)
    for rank, doc_id in enumerate(hit_doc_ids, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def _percentile(ts: list[float], q: float) -> float:
    """nearest-rank 百分位：ceil(q% × n) 处的值（1 起始），越界取末位。"""
    n = len(ts)
    rank = max(1, math.ceil(q / 100 * n))
    return ts[min(n - 1, rank - 1)]


def latency_stats(times: Iterable[float]) -> dict[str, float]:
    """检索延迟汇总：mean / p50 / p95（空输入返回全 0）。"""
    ts = sorted(times)
    if not ts:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0}
    return {
        "mean": mean(ts),
        "p50": _percentile(ts, 50.0),
        "p95": _percentile(ts, 95.0),
    }
