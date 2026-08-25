"""评测指标单元测试：est_tokens / recall_at_k / mrr / latency_stats。"""

from knowledge_pilot.rag.eval.metrics import est_tokens, latency_stats, mrr, recall_at_k


def test_est_tokens_heuristic():
    # 约 2 字符/token，下限 1
    assert est_tokens("") == 1
    assert est_tokens("a") == 1
    assert est_tokens("ab") == 1
    assert est_tokens("abc") == 2
    assert est_tokens("abcdefghij") == 5
    assert est_tokens("中文") == 1
    assert est_tokens("RAG 检索增强生成。") == 6  # ceil(11/2)


def test_recall_at_k_dedup_documents():
    # 同一文档多个 chunk 只算一次
    hit_docs = ["doc_a", "doc_a", "doc_b"]
    assert recall_at_k(hit_docs, ["doc_a"], 3) == 1.0
    # K 截断
    assert recall_at_k(["doc_a", "doc_b"], ["doc_a"], 1) == 1.0
    assert recall_at_k(["doc_b", "doc_a"], ["doc_a"], 1) == 0.0
    # 部分命中
    assert recall_at_k(["doc_a", "doc_b"], ["doc_a", "doc_c"], 2) == 0.5
    # 边界
    assert recall_at_k(["doc_a"], [], 3) == 0.0
    assert recall_at_k(["doc_a"], ["doc_a"], 0) == 0.0


def test_mrr():
    assert mrr(["doc_b", "doc_a"], ["doc_a"]) == 0.5  # 第 2 名命中
    assert mrr(["doc_a", "doc_b"], ["doc_a"]) == 1.0  # 第 1 名命中
    assert mrr(["doc_b", "doc_c"], ["doc_a"]) == 0.0  # 无命中
    # 重复 chunk 命中不重复计分：首个相关文档位置决定 MRR
    assert mrr(["doc_b", "doc_a", "doc_a"], ["doc_a"]) == 0.5


def test_latency_stats():
    assert latency_stats([]) == {"mean": 0.0, "p50": 0.0, "p95": 0.0}
    stats = latency_stats([3.0, 1.0, 2.0])  # 无序输入，需排序
    assert stats["mean"] == 2.0
    assert stats["p50"] == 2.0
    assert stats["p95"] == 3.0
    # 大样本 p95 取 95% 分位
    times = list(range(1, 101))
    stats = latency_stats(times)
    assert stats["p50"] == 50.0
    assert stats["p95"] == 95.0
