"""Knowledge Graph 抽取层测试（FakeChatClient 脚本化，离线）。"""

from knowledge_pilot.kg.extract import (
    KG_EXTRACT_PROMPT,
    build_kg_context,
    extract_entities_relations,
    format_triples,
)
from tests.fakes import FakeChatClient

KG_EXTRACT_JSON = (
    '{"entities": [{"name": "RAG", "type": "concept"}, '
    '{"name": "Embedding", "type": "method"}], '
    '"relations": [{"source": "RAG", "target": "Embedding", "relation": "uses"}]}'
)


def _llm(script):
    llm = FakeChatClient(script=[(["占位"], [])])
    llm.complete_script = script
    return llm


async def test_extract_splits_correctly():
    llm = _llm([KG_EXTRACT_JSON])
    entities, relations = await extract_entities_relations(llm, "RAG 依赖 Embedding 生成向量。")
    assert entities == [{"name": "RAG", "type": "concept"}, {"name": "Embedding", "type": "method"}]
    assert relations == [{"source": "RAG", "target": "Embedding", "relation": "uses"}]
    assert llm.seen_response_formats and llm.seen_response_formats[-1] == {"type": "json_object"}


async def test_extract_empty_text_skips_llm():
    llm = _llm([KG_EXTRACT_JSON])
    assert await extract_entities_relations(llm, "   ") == ([], [])
    assert llm.complete_calls == 0


async def test_extract_malformed_returns_empty():
    llm = _llm(["这不是 JSON"])
    assert await extract_entities_relations(llm, "资料") == ([], [])


async def test_extract_filters_invalid_structure():
    llm = _llm(
        [
            '{"entities": [{"name": "RAG", "type": "concept"}, {"name": ""}, 123], '
            '"relations": [{"source": "RAG", "target": "Embedding", "relation": "uses"}, '
            '{"source": "", "target": "X", "relation": "r"}, "垃圾"]}'
        ]
    )
    entities, relations = await extract_entities_relations(llm, "资料")
    assert entities == [{"name": "RAG", "type": "concept"}]
    assert relations == [{"source": "RAG", "target": "Embedding", "relation": "uses"}]


async def test_extract_llm_error_returns_empty():
    class _Raising:
        async def complete(self, *a, **k):
            raise RuntimeError("API 挂了")

    assert await extract_entities_relations(_Raising(), "资料") == ([], [])


def test_prompt_contains_json_word():
    assert "json" in KG_EXTRACT_PROMPT.lower()


def test_format_triples_and_build_kg_context():
    triples = [("rag", "uses", "embedding")]
    assert format_triples(triples) == "- rag -[uses]-> embedding"
    ctx = build_kg_context(triples)
    assert "相关实体关系（知识图谱）" in ctx
    assert "- rag -[uses]-> embedding" in ctx
    assert build_kg_context([]) == ""
