"""Memory 存储：落库 / 关键词召回 / 最近 / 跨实例持久化（tmp_path 临时库，全离线）。"""

from knowledge_pilot.memory import create_memory_store
from knowledge_pilot.memory.context import NO_REPORT, _run_head, build_memory_context
from knowledge_pilot.memory.store import RECALL_TEXT_LIMIT, _recall_text, tokenize


def _store(tmp_path, name="memory.db"):
    return create_memory_store(str(tmp_path / name))


def _run(report="", sources=None, **kw):
    store_kw = dict(plan=[], evidence=[], report=report, sources=sources or [])
    store_kw.update(kw)
    return store_kw


def test_tokenize_mixed():
    tokens = tokenize("RAG 混合搜索")
    assert "rag" in tokens
    assert "混合" in tokens
    assert "合搜" in tokens
    assert "搜索" in tokens


def test_save_and_count(tmp_path):
    store = _store(tmp_path)
    run_id = store.save_run("RAG chunking 策略", **_run(report="对比"))
    assert run_id
    assert store.count() == 1
    store.close()


def test_save_accepts_evidence_objects(tmp_path):
    """evidence 支持 duck-typed 对象（agent.graph.EvidenceItem），无需 import graph。"""
    store = _store(tmp_path)

    class _Ev:
        source = "https://x.example"
        title = "标题"
        snippet = "摘录"

    store.save_run("研究问题", plan=[], evidence=[_Ev()], report="报告", sources=[])
    run = store.recent(1)[0]
    assert run["evidence"][0]["source"] == "https://x.example"
    store.close()


def test_search_recalls_relevant(tmp_path):
    store = _store(tmp_path)
    store.save_run("RAG 的 chunking 策略", **_run(report="fixed 与 recursive 对比"))
    store.save_run("LangGraph 状态图", **_run(report="State Node Edge 编排"))
    hits = store.search("chunking 策略对比", top_k=3)
    assert [h["query"] for h in hits] == ["RAG 的 chunking 策略"]
    store.close()


def test_search_ignores_unrelated_and_empty(tmp_path):
    store = _store(tmp_path)
    assert store.search("任何问题") == []  # 空库
    store.save_run("向量数据库选型", **_run(report="Milvus 与 Faiss 对比"))
    assert store.search("今晚吃什么") == []  # 不相关
    store.close()


def test_recent_orders_newest_first(tmp_path):
    store = _store(tmp_path)
    store.save_run("主题A", **_run(report="A"))
    store.save_run("主题B", **_run(report="B"))
    assert [r["query"] for r in store.recent(10)] == ["主题B", "主题A"]
    store.close()


def test_persists_across_reopen(tmp_path):
    path = str(tmp_path / "memory.db")
    s1 = create_memory_store(path)
    s1.save_run("RAG 优化", **_run(report="hybrid + rerank"))
    s1.close()

    s2 = create_memory_store(path)  # 重开同一文件：数据仍在
    assert s2.count() == 1
    assert s2.search("RAG 优化方案")[0]["query"] == "RAG 优化"
    s2.close()


def test_close_idempotent(tmp_path):
    store = _store(tmp_path)
    store.close()
    store.close()  # 不抛异常


def test_search_recalls_by_plan_and_evidence(tmp_path):
    """plan / evidence 参与匹配（第六轮）。

    图谱模式的生成不再写报告，`report` 只剩一句结论；只按 `query + report + sources`
    匹配的话，召回会退化成「几乎什么都匹配不上」——而这两列一直都在库里。
    """
    store = _store(tmp_path)
    store.save_run(
        "RAG 的 chunking 策略",
        **_run(
            plan=[{"title": "递归切分", "question": "recursive splitter 怎么调"}],
            evidence=[{"title": "", "snippet": "Rerank 用 CrossEncoder 精排"}],
        ),
    )
    store.save_run("LangGraph 状态图", **_run(report="State Node Edge 编排"))

    assert [h["query"] for h in store.search("递归切分怎么做")] == ["RAG 的 chunking 策略"]
    assert [h["query"] for h in store.search("CrossEncoder 精排")] == ["RAG 的 chunking 策略"]
    store.close()


def test_recall_text_is_capped():
    """匹配文本有上限：一段超长摘录不该让每次召回都去 set() 上万个 token。"""
    run = {
        "query": "q",
        "plan": [],
        "evidence": [{"snippet": "字" * 10_000}],
        "report": "",
        "sources": [],
    }
    assert len(_recall_text(run)) == RECALL_TEXT_LIMIT


def test_recall_text_survives_junk_plan_and_evidence():
    """两列都是 JSON 存回来的，什么都可能是（字符串、null、嵌套）。匹配不许抛异常。"""
    run = {
        "query": "q",
        "plan": ["字符串计划", None, 42, {"title": "标题"}],
        "evidence": ["裸字符串", None, {"snippet": "摘录"}],
        "report": "",
        "sources": [],
    }
    text = _recall_text(run)
    assert "字符串计划" in text and "标题" in text and "摘录" in text


def test_build_memory_context():
    runs = [
        {
            "id": "a",
            "query": "RAG chunking",
            "plan": [],
            "evidence": [],
            "report": "# 报告\nfixed vs recursive",
            "sources": [{"title": "t", "url": "https://x.example"}],
            "created_at": "2026-09-01T10:00:00+08:00",
        }
    ]
    ctx = build_memory_context(runs)
    assert "历史研究背景" in ctx
    assert "RAG chunking" in ctx
    assert "https://x.example" in ctx
    assert build_memory_context([]) == ""


def test_build_memory_context_falls_back_to_the_plan_when_the_report_is_empty():
    """报告为空时显示研究计划，而不是「（无报告）」。

    图谱模式的历史记录现在报告很短甚至没有（那句结论才是 `report`），更早的记录里
    报告也可能空着；那时显示「（无报告）」等于告诉 planner「这条记录什么也没有」，
    可它明明有 query、有计划和证据。计划是这一路上**一定有**的东西。
    """
    runs = [
        {
            "id": "a",
            "query": "RAG chunking",
            "plan": [{"title": "递归切分"}, {"title": "语义分块"}],
            "evidence": [],
            "report": "",
            "sources": [],
            "created_at": "2026-09-01T10:00:00+08:00",
        }
    ]
    ctx = build_memory_context(runs)
    assert "计划：递归切分、语义分块" in ctx
    assert NO_REPORT not in ctx


def test_build_memory_context_still_says_no_report_without_a_plan():
    runs = [
        {
            "id": "a",
            "query": "q",
            "plan": [],
            "evidence": [],
            "report": "",
            "sources": [],
            "created_at": "2026-09-01T10:00:00+08:00",
        }
    ]
    assert NO_REPORT in build_memory_context(runs)


def test_run_head_prefers_the_report_over_the_plan():
    """有报告时还是报告优先——计划只是兜底，不是替换。"""
    run = {"report": "报告首行", "plan": [{"title": "计划标题"}]}
    assert _run_head(run) == "报告首行"
