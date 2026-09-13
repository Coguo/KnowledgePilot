"""MCP memory server 的「现开现关」读写语义（Phase 8）。

server 进程长驻复用后，不再缓存进程级 store 单例——每次 tool call 现开现关
（`open_read_store`）。这保证两件事，都在此锁定：
1. 父进程在两请求之间写库后，下一次 tool call 能读到**新**数据（不缓存旧连接/旧快照）；
2. 子进程不在 Windows 上常驻 db 文件句柄（父进程/测试删除或重建文件不 PermissionError）。

纯 stdlib sqlite3，不 import mcp —— 离线沙箱可跑。
"""

import os

import pytest

from knowledge_pilot.memory.store import ResearchMemoryStore, open_read_store


def _write(db_path, query: str, report: str = "") -> None:
    store = ResearchMemoryStore(db_path)
    try:
        store.save_run(query, report=report)
    finally:
        store.close()


def test_open_read_store_requires_path():
    with pytest.raises(ValueError):
        open_read_store("")


def test_fresh_open_sees_writes_made_after_first_open(tmp_path):
    """核心防回归：长驻 server 每次现开必须读到父进程的新写入。"""
    db = str(tmp_path / "mem.db")
    _write(db, "RAG chunking 策略", report="fixed-size 与 recursive 的取舍")

    first = open_read_store(db)
    try:
        assert first.count() == 1
    finally:
        first.close()

    # 父进程在两次 tool call 之间又写了一条。
    _write(db, "MCP 进程复用", report="长驻 stdio 子进程")

    second = open_read_store(db)  # 现开 → 读到新数据
    try:
        assert second.count() == 2
        runs = second.search("MCP", top_k=5)
        assert any("MCP" in r["query"] for r in runs)
    finally:
        second.close()


def test_reopening_after_every_write_never_goes_stale(tmp_path):
    """模拟 3 次 tool call：每次现开都应看到累计的最新条数。"""
    db = str(tmp_path / "mem.db")
    for i in range(1, 4):
        _write(db, f"第 {i} 个研究问题", report=f"报告 {i}")
        store = open_read_store(db)
        try:
            assert store.count() == i
        finally:
            store.close()


def test_open_read_store_search_is_functional(tmp_path):
    db = str(tmp_path / "mem.db")
    _write(db, "向量检索召回率", report="Hybrid 搜索 + Rerank 提升")

    store = open_read_store(db)
    try:
        runs = store.search("向量检索", top_k=3)
        assert len(runs) == 1
        assert runs[0]["query"] == "向量检索召回率"
        assert runs[0]["report"] == "Hybrid 搜索 + Rerank 提升"
    finally:
        store.close()


def test_closed_store_releases_file_handle(tmp_path):
    """Windows：close 后不应残留 db 文件句柄（否则下次删除/重建会 PermissionError）。"""
    db = str(tmp_path / "mem.db")
    _write(db, "临时研究")

    store = open_read_store(db)
    store.close()

    os.remove(db)  # 句柄已释放才能删掉
    assert not os.path.exists(db)


def test_multiple_fresh_stores_are_independent(tmp_path):
    db = str(tmp_path / "mem.db")
    _write(db, "共享库")

    a = open_read_store(db)
    b = open_read_store(db)
    try:
        assert a.count() == 1
        assert b.count() == 1
        a.close()  # 关掉一个不影响另一个
        assert b.count() == 1
    finally:
        b.close()
