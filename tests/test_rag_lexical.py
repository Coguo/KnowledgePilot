"""BM25 词法索引：tokenize 分词（无需 rank_bm25）/ Bm25Index 检索（懒加载依赖）。"""

import sys

import pytest

from knowledge_pilot.rag.lexical import Bm25Index, tokenize
from knowledge_pilot.rag.documents import Chunk


def test_module_import_does_not_import_rank_bm25():
    """模块本身可 import，不触发 rank_bm25 重依赖（懒加载保证）。"""
    assert "rank_bm25" not in sys.modules


# ---- tokenize：纯函数，不依赖 rank_bm25 ---------------------------------


def test_tokenize_latin_words_lowercased():
    assert tokenize("RAG and LlamaIndex") == ["rag", "and", "llamaindex"]


def test_tokenize_cjk_bigrams():
    assert tokenize("混合搜索") == ["混合", "合搜", "搜索"]


def test_tokenize_single_cjk_char():
    assert tokenize("好") == ["好"]


def test_tokenize_mixed_chinese_latin():
    assert tokenize("RAG系统") == ["rag", "系统"]


def test_tokenize_kana_bigrams():
    assert tokenize("こんにちは") == ["こん", "んに", "にち", "ちは"]


# ---- Bm25Index：需要 rank_bm25，未安装时整体跳过 -------------------------


def _chunk(text: str, index: int, doc_id: str = "doc1") -> Chunk:
    return Chunk(
        document_id=doc_id,
        chunk_id=f"{doc_id}:{index}",
        source="web",
        url="https://example.com/a",
        title="标题",
        text=text,
        metadata={"chunk_index": str(index), "chunk_total": "2"},
    )


class TestBm25Index:
    def setup_method(self):
        pytest.importorskip("rank_bm25")

    def test_empty_index_returns_no_hits(self):
        assert Bm25Index().search("查询", top_k=5) == []

    def test_matching_chunk_ranks_first(self):
        index = Bm25Index()
        index.add_chunks(
            [_chunk("深度学习与向量检索技术", 0), _chunk("股票市场行情分析", 1)]
        )
        hits = index.search("向量检索", top_k=2)

        assert len(hits) == 1
        assert hits[0].chunk.chunk_id == "doc1:0"
        assert hits[0].score > 0

    def test_no_match_returns_empty(self):
        index = Bm25Index()
        index.add_chunks([_chunk("深度学习与向量检索技术", 0)])
        assert index.search("量子计算", top_k=5) == []

    def test_add_chunks_after_search_is_reflected(self):
        index = Bm25Index()
        index.add_chunks([_chunk("深度学习与向量检索技术", 0)])
        assert index.search("股票", top_k=5) == []

        index.add_chunks([_chunk("股票市场行情分析", 1)])  # dirty 重建
        hits = index.search("股票", top_k=5)
        assert hits and hits[0].chunk.chunk_id == "doc1:1"
