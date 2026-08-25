"""评测运行器测试：数据集加载 / Spec 矩阵 / 离线确定性 / 组件接线。"""

import json
from pathlib import Path

from knowledge_pilot.rag.eval.dataset import EvalDataset, EvalDoc, EvalItem, load_dataset
from knowledge_pilot.rag.eval.offline import (
    DeterministicEmbedder,
    DeterministicLexicalIndex,
    KeywordQueryRewriter,
)
from knowledge_pilot.rag.eval.runner import (
    EvalComponents,
    Spec,
    all_specs,
    format_results_table,
    make_offline_components,
    run_eval,
)

FIXTURE = Path(__file__).parent / "fixtures" / "eval" / "small.json"


def test_load_dataset_fixture():
    dataset = load_dataset(FIXTURE)
    assert isinstance(dataset, EvalDataset)
    assert len(dataset.items) == 3
    item = dataset.items[0]
    assert item.query
    assert item.relevant_doc_ids == ["doc_rag"]
    doc_ids = {d.document_id for d in item.docs}
    assert "doc_rag" in doc_ids  # 相关文档必须入库
    assert "doc_llm" in doc_ids
    assert len(item.docs) >= 3  # 有多篇干扰文档，top_k 才有区分度


def test_load_dataset_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"items": [{"query": "只有 query"}]}), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="relevant_doc_ids"):
        load_dataset(bad)


def test_all_specs_16_rows():
    specs = all_specs()
    assert len(specs) == 16
    assert len({s for s in specs}) == 16  # 无重复
    assert {"fixed", "recursive"} == {s.chunk for s in specs}
    assert {"vector", "hybrid"} == {s.retrieval for s in specs}
    assert {"no", "rerank"} == {s.rerank for s in specs}
    assert {"original", "rewritten"} == {s.rewrite for s in specs}


async def test_run_eval_offline_full_matrix():
    dataset = load_dataset(FIXTURE)
    results = await run_eval(dataset, comp=make_offline_components(), top_k=3)

    assert len(results) == 16
    for r in results:
        assert 0.0 <= r.recall <= 1.0
        assert 0.0 <= r.mrr <= 1.0
        assert r.latency_p50 >= 0.0
        assert r.latency_p95 >= 0.0
        assert r.token_cost > 0  # 有上下文必然耗 token


async def test_run_eval_spec_filter():
    dataset = load_dataset(FIXTURE)
    comp = make_offline_components()
    only_recursive = lambda s: s.chunk == "recursive"  # noqa: E731
    results = await run_eval(dataset, comp=comp, top_k=3, spec_filter=only_recursive)
    assert len(results) == 8
    assert all(r.spec.chunk == "recursive" for r in results)

    empty = await run_eval(
        dataset, comp=comp, top_k=3, spec_filter=lambda s: s.chunk == "nope"
    )
    assert empty == []


async def test_run_eval_deterministic():
    dataset = load_dataset(FIXTURE)
    comp = make_offline_components()
    first = await run_eval(dataset, comp=comp, top_k=3)
    second = await run_eval(dataset, comp=comp, top_k=3)
    for a, b in zip(first, second):
        assert a.recall == b.recall
        assert a.mrr == b.mrr
        assert a.token_cost == b.token_cost


async def test_hybrid_consults_lexical_vector_does_not():
    """接线验证：hybrid spec 每 item 调用词法索引，vector spec 不调用。"""
    class _RecordingLexical(DeterministicLexicalIndex):
        def __init__(self, counter: list[int]):
            super().__init__()
            self.counter = counter

        def search(self, query, top_k):
            self.counter[0] += 1
            return super().search(query, top_k)

    dataset = load_dataset(FIXTURE)
    counter: list[int] = [0]
    comp = EvalComponents(
        embedder=DeterministicEmbedder(),
        lexical_factory=lambda: _RecordingLexical(counter),
        reranker=None,
        rewriter_llm=KeywordQueryRewriter(),
    )

    hybrid_spec = Spec("fixed", "hybrid", "no", "original")
    await run_eval(
        dataset, comp=comp, top_k=2, rerank_candidates=5,
        spec_filter=lambda s: s == hybrid_spec,
    )
    assert counter[0] == len(dataset.items)

    vector_spec = Spec("fixed", "vector", "no", "original")
    await run_eval(
        dataset, comp=comp, top_k=2, rerank_candidates=5,
        spec_filter=lambda s: s == vector_spec,
    )
    assert counter[0] == len(dataset.items)  # vector spec 不新增词法调用


async def test_run_item_rewritten_uses_rewriter():
    """rewrite=rewritten 轴真实走改写器（keyword 改写）而非原查询。"""
    rewritten_queries: list[str] = []

    class _CaptureRewriter(KeywordQueryRewriter):
        async def rewrite(self, query: str) -> str:
            rewritten_queries.append(query)
            return await super().rewrite(query)

    dataset = load_dataset(FIXTURE)
    comp = EvalComponents(
        embedder=DeterministicEmbedder(),
        lexical_factory=DeterministicLexicalIndex,
        reranker=None,
        rewriter_llm=_CaptureRewriter(),
    )
    spec = Spec("fixed", "vector", "no", "rewritten")
    await run_eval(
        dataset, comp=comp, top_k=2, rerank_candidates=5,
        spec_filter=lambda s: s == spec,
    )
    assert len(rewritten_queries) == len(dataset.items)
    assert all(q for q in rewritten_queries)  # 非空改写


async def test_format_results_table():
    dataset = load_dataset(FIXTURE)
    results = await run_eval(
        dataset, comp=make_offline_components(), top_k=3,
        spec_filter=lambda s: s == Spec("recursive", "hybrid", "rerank", "rewritten"),
    )
    table = format_results_table(results)
    assert "chunk" in table
    assert "recursive" in table
    assert "hybrid" in table
    assert "rerank" in table
    assert "rewritten" in table
