"""Knowledge Graph 存储层测试（纯 stdlib，离线，无需 langgraph）。"""

from knowledge_pilot.kg.graph import GraphStore, match_query_entities, tokenize


def test_tokenize_mixed():
    assert tokenize("RAG 混合搜索") == ["rag", "混合", "合搜", "搜索"]


def test_add_entities_and_counts():
    store = GraphStore()
    store.add_entities([{"name": "RAG", "type": "concept"}, {"name": "Embedding", "type": "method"}])
    assert store.node_count() == 2
    assert store.edge_count() == 0


def test_add_entities_normalizes_and_dedups():
    store = GraphStore()
    store.add_entities([{"name": "  RAG  ", "type": "concept"}, {"name": "rag", "type": "concept"}])
    assert store.node_count() == 1


def test_add_relations_auto_creates_endpoints():
    store = GraphStore()
    store.add_relations([{"source": "RAG", "target": "Embedding", "relation": "uses"}])
    assert store.node_count() == 2
    assert store.edge_count() == 1


def test_add_relations_skips_empty_and_dedups():
    store = GraphStore()
    store.add_relations(
        [
            {"source": "", "target": "B", "relation": "r"},
            {"source": "A", "target": "B", "relation": "r"},
            {"source": "A", "target": "B", "relation": "r"},  # 重复 → 去重
            {"source": "A", "target": "", "relation": "r"},
        ]
    )
    assert store.edge_count() == 1
    assert store.node_count() == 2


def test_add_relations_normalizes_endpoints():
    store = GraphStore()
    store.add_relations([{"source": "RAG", "target": "Embedding", "relation": "uses"}])
    store.add_relations([{"source": "rag", "target": "embedding", "relation": "uses"}])
    assert store.edge_count() == 1
    assert store.node_count() == 2


def test_query_hops_expand_and_dedup():
    store = GraphStore()
    store.add_relations(
        [
            {"source": "A", "target": "B", "relation": "r"},
            {"source": "B", "target": "C", "relation": "r"},
            {"source": "C", "target": "D", "relation": "r"},
        ]
    )
    assert store.query(["A"], hops=1) == [("a", "r", "b")]
    assert store.query(["A"], hops=2) == [("a", "r", "b"), ("b", "r", "c")]
    assert store.query(["A"], hops=3) == [("a", "r", "b"), ("b", "r", "c"), ("c", "r", "d")]


def test_query_captures_incoming_edges():
    store = GraphStore()
    store.add_relations([{"source": "A", "target": "B", "relation": "r"}])
    # 从 target 出发沿入边也能命中（拿到 A → B）
    assert store.query(["B"], hops=1) == [("a", "r", "b")]


def test_query_no_match_and_isolated_node():
    store = GraphStore()
    store.add_entities([{"name": "孤立", "type": "concept"}])
    assert store.query(["不存在"], hops=2) == []
    assert store.query(["孤立"], hops=2) == []  # 有实体无关系 → 无三元组


def test_query_clamps_hops():
    store = GraphStore()
    store.add_relations([{"source": "A", "target": "B", "relation": "r"}])
    # KG_HOPS=0/-1 误配时按 1 层处理，仍能拿到直接相连关系
    assert store.query(["A"], hops=0) == [("a", "r", "b")]
    assert store.query(["A"], hops=-1) == [("a", "r", "b")]


def test_match_query_entities_keyword_overlap():
    store = GraphStore()
    store.add_entities(
        [
            {"name": "RAG", "type": "concept"},
            {"name": "Embedding", "type": "method"},
            {"name": "Chunking", "type": "method"},
        ]
    )
    matched = match_query_entities(store, "RAG 与 Embedding 的关系")
    assert "rag" in matched and "embedding" in matched
    assert "chunking" in match_query_entities(store, "chunk 策略")  # 词干：chunk ⊆ chunking
    assert match_query_entities(store, "完全无关主题") == []
