"""Agent Evaluation 数据集 A 轨测试：加载 + 位置化 ValueError（纯 stdlib）。"""

import json
from pathlib import Path

import pytest

from knowledge_pilot.agent.eval.dataset import (
    KNOWN_VARIANT_NAMES,
    VALID_PROFILES,
    GoldToolCall,
    load_dataset,
)

FIXTURE = Path("tests/fixtures/eval_agent/small.json")


def test_fixture_loads_and_fields():
    ds = load_dataset(FIXTURE)
    assert len(ds.items) == 4
    item = ds.items[0]
    assert item.query.startswith("GraphRAG")
    assert item.must_include == ("GraphRAG", "RAG", "知识图谱")
    assert item.required_tools == ("search_web",)
    assert item.expected_tool_calls == (
        GoldToolCall(name="search_web", arguments={"query": "GraphRAG 与 RAG 的区别"}),
    )
    assert item.offline_profile == "ideal"
    assert item.variants is None  # 缺省 = 全部五档
    assert item.pre_seed == ()
    assert [a.name for a in item.aspects] == ["cites_sources", "defines_both"]


def test_fixture_mcp_and_memory_items():
    ds = load_dataset(FIXTURE)
    mcp_item = ds.items[2]
    assert mcp_item.offline_profile == "mcp_only_tool"
    assert mcp_item.required_tools == ("search_papers",)
    assert [a.name for a in mcp_item.aspects] == ["cites_papers"]

    mem_item = ds.items[3]
    assert len(mem_item.pre_seed) == 1
    seed = mem_item.pre_seed[0]
    assert "评测指标" in seed.query
    assert len(seed.sources) == 2


def _write(payload) -> Path:
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".json", prefix="eval_agent_")
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return Path(path)


def _minimal_item(**overrides):
    item = {
        "query": "RAG 是什么",
        "must_include": ["RAG"],
        "required_tools": ["search_web"],
        "expected_tool_calls": [
            {"name": "search_web", "arguments": {"query": "RAG"}}
        ],
    }
    item.update(overrides)
    return item


def _load_one(item_dict):
    p = _write({"items": [item_dict]})
    try:
        return load_dataset(p)
    finally:
        p.unlink()


def test_whitelists_expected():
    assert KNOWN_VARIANT_NAMES == frozenset(
        {"loop", "graph", "graph+memory", "graph+kg", "all"}
    )
    assert VALID_PROFILES == frozenset(
        {"ideal", "iterate_to_cap", "tool_round_cap", "planner_bad_json", "mcp_only_tool"}
    )


def test_missing_query_raises_positioned():
    with pytest.raises(ValueError, match=r"items\[0\]\.query"):
        _load_one(_minimal_item(query=None))


def test_empty_items_raises():
    p = _write({"items": []})
    try:
        with pytest.raises(ValueError, match="非空的 items"):
            load_dataset(p)
    finally:
        p.unlink()


def test_expected_tool_calls_type_error_raises():
    with pytest.raises(ValueError, match=r"expected_tool_calls"):
        _load_one(_minimal_item(expected_tool_calls=[{"name": "search_web"}]))  # 缺 arguments


def test_expected_tool_calls_arguments_non_object_raises():
    with pytest.raises(ValueError, match=r"arguments"):
        _load_one(
            _minimal_item(
                expected_tool_calls=[{"name": "search_web", "arguments": "not-a-dict"}]
            )
        )


def test_unknown_variant_member_raises():
    with pytest.raises(ValueError, match=r"未知变体 'bogus'"):
        _load_one(_minimal_item(variants=["loop", "bogus"]))


def test_variants_subset_respected():
    ds = _load_one(_minimal_item(variants=["loop", "graph"]))
    assert ds.items[0].variants == ("loop", "graph")


def test_invalid_offline_profile_raises():
    with pytest.raises(ValueError, match=r"offline_profile"):
        _load_one(_minimal_item(offline_profile="not_a_profile"))


def test_pre_seed_malformed_raises():
    with pytest.raises(ValueError, match=r"pre_seed\[0\]"):
        _load_one(
            _minimal_item(pre_seed=[{"query": "只有 query 没有 report"}])
        )


def test_aspect_missing_name_raises():
    with pytest.raises(ValueError, match=r"aspects\[0\]\.name"):
        _load_one(_minimal_item(aspects=[{"question": "问题"}]))
