"""Embedder：Fake 契约、BGE-M3 维度、懒加载验证、进程级单例。"""

import sys

from knowledge_pilot.rag.embedder import BGEM3Embedder, get_shared_embedder

from tests.fakes import FakeEmbedder


def test_bge3_dimensions():
    assert BGEM3Embedder().dimensions == 1024


def test_lazy_import_no_sentence_transformers():
    sys.modules.pop("sentence_transformers", None)
    BGEM3Embedder()  # 构造不应触发重依赖 import
    assert "sentence_transformers" not in sys.modules


def test_shared_embedder_singleton():
    class Cfg:
        embedding_model = "BAAI/bge-m3"
        embedding_cache_dir = ""
        embedding_device = "cpu"

    assert get_shared_embedder(Cfg()) is get_shared_embedder(Cfg())


def test_fake_embedder_deterministic_and_shape():
    e = FakeEmbedder(dimensions=8)
    v1 = e.embed(["相同文本", "相同文本"])
    v2 = e.embed(["相同文本"])

    assert len(v1) == 2
    assert all(len(v) == 8 for v in v1)
    assert v1[0] == v1[1]  # 相同文本 → 相同向量
    assert v1[0] == v2[0]  # 跨调用确定性
    assert e.calls == [["相同文本", "相同文本"], ["相同文本"]]
