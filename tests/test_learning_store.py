"""Phase 9 M3：学习持久化底座（SQLite 结构与状态 + Markdown 正文）。

这一层是「重启不重新检索」的地基，所以测试重点是**跨实例读回**——每次都新建一个
store 实例（模拟重启），而不是复用同一个连接。
"""

import os
import re

import pytest

from knowledge_pilot.config import Settings
from knowledge_pilot.learning import create_learning_store
from knowledge_pilot.learning.notes import (
    OUTLINE_SECTION,
    POINTS_SECTION,
    USER_SECTION,
    append_explanation,
    ensure_node_note,
    heading_index,
    insert_outline,
    note_filename,
    note_path,
    parse_front_matter,
    read_outline,
    read_text,
    read_user_notes,
    render_outline,
    replace_outline,
    report_path,
    slugify,
    topic_dir_name,
    update_note_status,
    write_text,
    write_user_notes,
)


@pytest.fixture
def store(tmp_path):
    s = create_learning_store(str(tmp_path / "learning.db"))
    yield s
    s.close()


def _graph(store, topic_id, nodes, edges=()):
    return store.replace_graph(
        topic_id,
        nodes=[
            {"name": n, "type": "概念", "summary": f"{n} 的说明",
             "key_points": [f"{n} 要点"], "depth": d, "order_index": i}
            for i, (n, d) in enumerate(nodes)
        ],
        edges=[{"source": s, "target": t, "relation": "前置"} for s, t in edges],
    )


# ---- 主题 ---------------------------------------------------------------


def test_topic_round_trips_across_instances(tmp_path):
    path = str(tmp_path / "learning.db")
    s1 = create_learning_store(path)
    topic = s1.create_topic("RAG 的 chunking 策略")
    s1.update_topic(topic["id"], status="ready", summary="固定 vs 递归")
    s1.close()

    s2 = create_learning_store(path)  # 模拟重启
    try:
        got = s2.get_topic(topic["id"])
    finally:
        s2.close()

    assert got["query"] == "RAG 的 chunking 策略"
    assert got["title"] == "RAG 的 chunking 策略"  # 未给 title 时回退用 query
    assert got["status"] == "ready"
    assert got["summary"] == "固定 vs 递归"


def test_new_topic_starts_empty_and_ready_to_retry(store):
    """建壳不调 LLM：立刻返回一个可点的条目，生成失败也还有壳能重试。"""
    topic = store.create_topic("RAG")
    assert topic["status"] == "empty"
    assert topic["error"] == ""
    assert store.get_graph(topic["id"])["nodes"] == []


def test_topic_status_whitelist_rejects_unknown(store):
    topic = store.create_topic("x")
    with pytest.raises(ValueError, match="未知主题状态"):
        store.update_topic(topic["id"], status="done")


def test_list_topics_is_newest_first(store):
    first = store.create_topic("第一个")
    second = store.create_topic("第二个")
    ids = [t["id"] for t in store.list_topics()]
    assert ids == [second["id"], first["id"]]


def test_progress_counts_and_percent(store):
    topic = store.create_topic("进度")
    _graph(store, topic["id"], [("A", 0), ("B", 1), ("C", 1), ("D", 2)])
    graph = store.get_graph(topic["id"])
    ids = {n["name"]: n["id"] for n in graph["nodes"]}

    store.set_node_status(ids["A"], "mastered")
    store.set_node_status(ids["B"], "recommended")

    progress = store.get_topic(topic["id"])["progress"]
    assert progress == {"total": 4, "mastered": 1, "recommended": 1,
                        "unlearned": 2, "percent": 25}


def test_empty_topic_progress_is_zero_not_full(store):
    """0 个知识点的主题算 0%，而不是「全部完成」（除零的两个错方向之一）。"""
    topic = store.create_topic("空主题")
    assert store.get_topic(topic["id"])["progress"]["percent"] == 0


# ---- 图：节点 / 边 / 复用 ----------------------------------------------


def test_replace_graph_persists_nodes_edges_and_order(store):
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("文本切分", 0), ("向量检索", 1), ("重排序", 2)],
           [("文本切分", "向量检索"), ("向量检索", "重排序")])

    graph = store.get_graph(topic["id"])
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索", "重排序"]
    assert [n["depth"] for n in graph["nodes"]] == [0, 1, 2]
    assert len(graph["edges"]) == 2
    # order 是给前端画图的推荐学习序（拓扑序）
    assert graph["order"] == [n["id"] for n in graph["nodes"]]


def test_replace_graph_reuses_node_identity_and_progress(store):
    """重生成图不丢学习进度——按 name 复用 node id 与状态。"""
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0), ("B", 1)])
    before = {n["name"]: n["id"] for n in store.get_graph(topic["id"])["nodes"]}
    store.set_node_status(before["A"], "mastered")
    store.add_message(before["A"], "user", "我懂了")

    result = _graph(store, topic["id"], [("A", 0), ("B", 1), ("C", 2)],
                    [("A", "B")])  # 这次多抽出一个 C
    after = {n["name"]: n["id"] for n in store.get_graph(topic["id"])["nodes"]}

    assert result["reused"] == 2  # A、B 复用
    assert after["A"] == before["A"] and after["B"] == before["B"]
    assert store.get_node_state(before["A"])["status"] == "mastered"  # 进度还在
    assert [m["content"] for m in store.list_messages(before["A"])] == ["我懂了"]
    assert after["C"] not in before.values()  # 新节点拿到新 id


def test_replace_graph_drops_nodes_that_disappeared(store):
    """新图里不再出现的知识点连同状态/对话一起删掉（它已经不存在了）。"""
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0), ("B", 1)])
    ids = {n["name"]: n["id"] for n in store.get_graph(topic["id"])["nodes"]}
    store.set_node_status(ids["B"], "mastered")
    store.add_message(ids["B"], "user", "关于 B 的讨论")

    _graph(store, topic["id"], [("A", 0)])

    assert store.get_node(ids["B"]) is None
    assert store.get_node_state(ids["B"]) is None
    assert store.list_messages(ids["B"]) == []


def test_replace_graph_skips_dangling_and_self_edges(store):
    """指向未知节点 / 自环的边被丢弃，不留半条悬空边。"""
    topic = store.create_topic("RAG")
    result = store.replace_graph(
        topic["id"],
        nodes=[{"name": "A", "depth": 0, "order_index": 0}],
        edges=[{"source": "A", "target": "A"}, {"source": "A", "target": "不存在"}],
    )
    assert result["edges"] == 0
    assert store.get_graph(topic["id"])["edges"] == []


def test_prerequisites_reported_by_name(store):
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0), ("B", 1), ("C", 2)],
           [("A", "C"), ("B", "C")])
    ids = {n["name"]: n["id"] for n in store.get_graph(topic["id"])["nodes"]}

    node = store.get_node(ids["C"])
    assert [p["name"] for p in node["prerequisites"]] == ["A", "B"]
    assert store.get_node(ids["A"])["prerequisites"] == []


# ---- 学习状态机 ---------------------------------------------------------


def test_status_transitions_follow_whitelist(store):
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0)])
    node_id = store.get_graph(topic["id"])["nodes"][0]["id"]

    assert store.set_node_status(node_id, "recommended", reason="讲清了", confidence=0.8) == "recommended"
    assert store.set_node_status(node_id, "mastered") == "mastered"
    assert store.set_node_status(node_id, "unlearned") == "unlearned"  # 撤销掌握


def test_illegal_status_transition_raises(store):
    """recommended → recommended 之外的非法跳转直接报错，不静默吞掉。"""
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0)])
    node_id = store.get_graph(topic["id"])["nodes"][0]["id"]

    with pytest.raises(ValueError, match="未知学习状态"):
        store.set_node_status(node_id, "done")


def test_same_status_is_idempotent_not_an_error(store):
    """重复点「确认掌握」不该报错。"""
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0)])
    node_id = store.get_graph(topic["id"])["nodes"][0]["id"]

    store.set_node_status(node_id, "mastered")
    assert store.set_node_status(node_id, "mastered") == "mastered"


def test_recommendation_metadata_recorded_and_cleared(store):
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0)])
    node_id = store.get_graph(topic["id"])["nodes"][0]["id"]

    store.set_node_status(node_id, "recommended", reason="解释了切分粒度", confidence=0.9)
    state = store.get_node_state(node_id)
    assert state["recommend_reason"] == "解释了切分粒度"
    assert state["confidence"] == 0.9
    assert state["recommended_at"]  # 有时间戳

    store.set_node_status(node_id, "unlearned")  # 用户驳回推荐
    state = store.get_node_state(node_id)
    assert state["recommend_reason"] == ""  # 不留幽灵状态
    assert state["confidence"] == 0
    assert state["recommended_at"] == ""


def test_set_status_on_unknown_node_raises(store):
    with pytest.raises(ValueError, match="知识点不存在"):
        store.set_node_status("nope", "mastered")


def test_chat_turns_accumulate(store):
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0)])
    node_id = store.get_graph(topic["id"])["nodes"][0]["id"]

    assert store.bump_chat_turns(node_id) == 1
    assert store.bump_chat_turns(node_id) == 2
    assert store.get_node_state(node_id)["chat_turns"] == 2


# ---- 对话 ---------------------------------------------------------------


def test_messages_round_trip_across_instances(tmp_path):
    path = str(tmp_path / "learning.db")
    s1 = create_learning_store(path)
    topic = s1.create_topic("RAG")
    _graph(s1, topic["id"], [("A", 0)])
    node_id = s1.get_graph(topic["id"])["nodes"][0]["id"]
    s1.add_message(node_id, "user", "什么是 chunking？")
    s1.add_message(node_id, "assistant", "把长文档切成小块。", sources=[{"title": "t", "url": "u"}])
    s1.close()

    s2 = create_learning_store(path)
    try:
        messages = s2.list_messages(node_id)
    finally:
        s2.close()

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "什么是 chunking？"
    assert messages[1]["sources"] == [{"title": "t", "url": "u"}]


# ---- 删除 ---------------------------------------------------------------


def test_delete_topic_removes_graph_but_keeps_notes_by_default(store, tmp_path):
    notes_dir = str(tmp_path / "notes")
    note_path = os.path.join(notes_dir, "a.md")
    os.makedirs(notes_dir, exist_ok=True)
    write_text(note_path, "# 我的笔记不该被顺手删掉")

    topic = store.create_topic("RAG")
    store.replace_graph(
        topic["id"],
        nodes=[{"name": "A", "depth": 0, "order_index": 0, "note_path": note_path}],
        edges=[],
    )

    returned = store.delete_topic(topic["id"])

    assert returned == [note_path]
    assert store.get_topic(topic["id"]) is None
    assert os.path.exists(note_path)  # 清库不删用户的学习资产


def test_delete_topic_cascades_children(store):
    topic = store.create_topic("RAG")
    _graph(store, topic["id"], [("A", 0), ("B", 1)], [("A", "B")])
    ids = {n["name"]: n["id"] for n in store.get_graph(topic["id"])["nodes"]}
    store.add_message(ids["A"], "user", "hi")

    store.delete_topic(topic["id"])

    assert store.get_node(ids["A"]) is None
    assert store.list_messages(ids["A"]) == []
    assert store.get_graph(topic["id"]) is None


# ---- Markdown 正文层 ----------------------------------------------------


def test_slugify_keeps_cjk_and_strips_windows_unsafe_chars():
    assert slugify("文本切分策略") == "文本切分策略"
    assert slugify("RAG chunking") == "RAG-chunking"
    assert slugify('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij"
    assert slugify("   ") == "untitled"
    assert slugify("") == "untitled"
    assert slugify("CON") == "untitled"  # Windows 保留设备名
    assert slugify("nul") == "untitled"


def test_slugify_truncates_long_names():
    assert len(slugify("字" * 200)) == 40


def test_note_filename_freezes_order_number():
    assert note_filename(0, "文本切分策略") == "01_文本切分策略.md"
    assert note_filename(9, "向量检索") == "10_向量检索.md"
    assert note_filename(99, "重排序") == "100_重排序.md"


def test_note_paths_shrink_mixed_separators():
    """`.env` 里配 `./data/knowledge`（正斜杠）时，拼接结果不能前后混用分隔符——
    这个串会原样落库并显示给用户看。"""
    mixed = note_path("./data/knowledge/sub", 0, "文本切分")
    assert "/" not in mixed or "\\" not in mixed
    assert mixed.endswith("01_文本切分.md")
    assert report_path("./data/knowledge/sub").endswith("report.md")


def test_topic_dir_name_is_stable_and_collision_free():
    a = topic_dir_name("RAG", "abcdef1234567890")
    assert a == "RAG-abcdef"
    # 同名不同 id → 不同目录（否则两个主题的正文会混在一个文件夹里）
    assert a != topic_dir_name("RAG", "9999991234567890")
    assert topic_dir_name("RAG", "abcdef1234567890") == a  # 稳定


def test_ensure_node_note_creates_then_never_overwrites(tmp_path):
    path = str(tmp_path / "01_A.md")
    meta = {"topic_id": "t1", "node_id": "n1", "order": 1, "type": "概念",
            "prerequisites": "无", "status": "unlearned"}

    assert ensure_node_note(path, meta=meta, title="A", key_points=["要点一"]) is True
    text = read_text(path)
    assert parse_front_matter(text)["node_id"] == "n1"
    assert "- 要点一" in text
    assert USER_SECTION in text

    # 用户手写笔记后重生成 → 文件必须原样保留
    write_text(path, text.replace("程序不会改写这一节", "我的心得体会"))
    assert ensure_node_note(path, meta=meta, title="A", key_points=["新要点"]) is False
    assert "我的心得体会" in read_text(path)
    assert "新要点" not in read_text(path)


def test_append_explanation_inserts_before_user_section(tmp_path):
    """新讲解插在「我的笔记」之前——用户区永远在末尾，程序只在它上面生长。"""
    path = str(tmp_path / "n.md")
    ensure_node_note(path, meta={"node_id": "n"}, title="A")
    write_text(path, read_text(path).replace("程序不会改写这一节", "我自己的笔记"))

    append_explanation(path, title="Q1", body="这是第一段讲解。", stamp="2026-09-10 10:00")
    append_explanation(path, title="Q2", body="这是第二段讲解。", stamp="2026-09-10 10:05")

    text = read_text(path)
    assert "这是第一段讲解。" in text and "这是第二段讲解。" in text
    assert text.index("第一段讲解") < text.index("第二段讲解")  # 只追加，保留历史
    assert text.index("第二段讲解") < text.index(USER_SECTION)  # 都在用户区之前
    assert "我自己的笔记" in text  # 用户笔记没被冲掉


def test_append_explanation_rebuilds_stub_when_file_missing(tmp_path):
    """文件被外部删掉时重建骨架，而不是静默丢弃这次讲解。"""
    path = str(tmp_path / "gone.md")
    append_explanation(path, title="Q", body="讲解内容", stamp="2026-09-10 10:00")

    text = read_text(path)
    assert "讲解内容" in text
    assert USER_SECTION in text  # 骨架里带上用户区，下次追加仍有锚点


def test_update_note_status_edits_front_matter(tmp_path):
    path = str(tmp_path / "n.md")
    ensure_node_note(path, meta={"node_id": "n", "status": "unlearned"}, title="A")

    assert update_note_status(path, "mastered") is True
    assert parse_front_matter(read_text(path))["status"] == "mastered"
    assert "# A" in read_text(path)  # 正文未被破坏

    assert update_note_status(str(tmp_path / "missing.md"), "mastered") is False


# ---- 提纲：按需插入的那一段 ---------------------------------------------


def test_heading_index_requires_a_whole_line(tmp_path):
    """正文里提一嘴「## 提纲」不算有提纲——否则判重会误判，读回会捞错东西。"""
    assert heading_index("a\n## 提纲\nb", OUTLINE_SECTION) > 0
    assert heading_index("### 提纲\n", OUTLINE_SECTION) < 0
    assert heading_index("详见上面的 ## 提纲 一节\n", OUTLINE_SECTION) < 0
    assert heading_index("# 提纲\n", OUTLINE_SECTION) < 0


def test_insert_outline_only_adds_and_never_rewrites(tmp_path):
    """核心不变量：除插入点外逐字保留，且插在 `## 要点` 之前。"""
    path = str(tmp_path / "n.md")
    ensure_node_note(path, meta={"node_id": "n"}, title="A", key_points=["要点一"])
    write_text(path, read_text(path).replace("程序不会改写这一节", "我自己的笔记"))
    before = read_text(path)

    assert insert_outline(path, ["第一步", "第二步"]) is True
    after = read_text(path)

    # 提纲在要点之前（文件顺序 = 提纲 → 要点 → 讲解记录 → 我的笔记）
    assert after.index(OUTLINE_SECTION) < after.index(POINTS_SECTION)
    assert after.index(OUTLINE_SECTION) < after.index(USER_SECTION)
    # 编号是有序列表（后端就是这么生成的），不是无序项目符号
    assert "1. 第一步" in after and "2. 第二步" in after
    # 删掉刚插进去的那一段，剩下的必须与插入前**逐字相同**
    start = after.index(OUTLINE_SECTION)
    end = after.index(POINTS_SECTION)
    assert after[:start] + after[end:] == before
    assert read_outline(path) == ["第一步", "第二步"]


def test_insert_outline_is_idempotent_and_leaves_no_empty_section(tmp_path):
    path = str(tmp_path / "n.md")
    ensure_node_note(path, meta={"node_id": "n"}, title="A")

    assert insert_outline(path, ["唯一条目"]) is True
    once = read_text(path)
    assert insert_outline(path, ["换一批条目"]) is False  # 已有 → 一个字节也不动
    assert read_text(path) == once

    # 空提纲不落盘：宁可不写，也不要在用户文件里留一个空的 `## 提纲`
    other = str(tmp_path / "m.md")
    ensure_node_note(other, meta={"node_id": "m"}, title="B")
    assert insert_outline(other, []) is False
    assert insert_outline(other, ["", "   "]) is False
    assert OUTLINE_SECTION not in read_text(other)

    # 文件不在 → False（调用方负责先补骨架）
    assert insert_outline(str(tmp_path / "missing.md"), ["x"]) is False


def test_read_outline_stops_at_the_next_section(tmp_path):
    """读回必须止于下一个二级标题——不然「我的笔记」里的列表会被当成提纲条目。"""
    path = str(tmp_path / "n.md")
    write_text(
        path,
        "---\nnode_id: n\n---\n\n# A\n\n"
        f"{OUTLINE_SECTION}\n\n1. 一\n2. 二\n\n{USER_SECTION}\n\n- 我自己写的\n",
    )
    assert read_outline(path) == ["一", "二"]
    assert read_outline(str(tmp_path / "none.md")) == []
    write_text(path, "# A\n\n没有提纲的文件\n")
    assert read_outline(path) == []


def test_insert_outline_appends_when_there_is_no_second_level_heading(tmp_path):
    """用户把文件删空了（连 `## ` 都没有）时接到末尾，而不是拼出半截文件。"""
    path = str(tmp_path / "n.md")
    write_text(path, "# 只有标题\n")
    assert insert_outline(path, ["第一步"]) is True
    text = read_text(path)
    assert text.startswith("# 只有标题\n")
    assert text.index(OUTLINE_SECTION) > text.index("# 只有标题")
    assert read_outline(path) == ["第一步"]


# ---- replace_outline：「重新生成」那一次换段（走查反馈 ②）----------------


def _note_with_outline(tmp_path, items=("老口径的一整句话", "老口径的第二条")):
    path = str(tmp_path / "n.md")
    ensure_node_note(path, meta={"node_id": "n"}, title="A", key_points=["要点一"])
    insert_outline(path, list(items))
    # 再往文件里加一段讲解 + 让用户改一笔笔记：它们都必须活过下面那次替换。
    append_explanation(path, title="讲解", body="这一轮的正文。")
    write_text(path, read_text(path).replace("程序不会改写这一节", "我自己写的笔记"))
    return path


def _strip_outline(text: str) -> str:
    """把 `## 提纲` 那一段整个摘掉——用来断言「其余字节逐字未变」。"""
    at = text.index(OUTLINE_SECTION)
    rest = text[at + len(OUTLINE_SECTION):]
    m = re.search(r"^##\s", rest, re.MULTILINE)
    return text[:at] + (rest[m.start():] if m else "")


def test_replace_outline_swaps_only_that_section(tmp_path):
    """核心不变量：换掉的是**程序自己写的那一段**，其余字节逐字保留。"""
    path = _note_with_outline(tmp_path)
    before = read_text(path)

    assert replace_outline(path, ["有哪些分块方法", "各自的切分规则"]) is True
    after = read_text(path)

    assert read_outline(path) == ["有哪些分块方法", "各自的切分规则"]
    assert "老口径的一整句话" not in after
    # 讲解记录与用户笔记都还在（这是与 `insert_outline` 同一级别的约束）
    assert "这一轮的正文。" in after
    assert "我自己写的笔记" in after
    # 摘掉提纲段之后，前后两份文件必须**逐字相同**
    assert _strip_outline(after) == _strip_outline(before)
    # 段的位置也没变：仍在要点之前
    assert after.index(OUTLINE_SECTION) < after.index(POINTS_SECTION)


def test_replace_outline_keeps_working_when_it_is_the_last_section(tmp_path):
    """提纲是文件里最后一段（后面没有 `^##`）时，替换不能把尾巴吃掉或多出半截。"""
    path = str(tmp_path / "m.md")
    write_text(path, "# B\n\n## 提纲\n\n1. 旧的\n")
    assert replace_outline(path, ["新的"]) is True
    assert read_text(path) == "# B\n\n## 提纲\n\n1. 新的\n"
    assert read_outline(path) == ["新的"]


def test_replace_outline_refuses_to_do_anything_else(tmp_path):
    """没有那一段 / 空条目 / 文件不在 —— 三种情况都必须返回 False 且**一个字节不写**。

    这里刻意与 `insert_outline` 分工：那一条只在**没有**提纲时动手，这一条只在
    **有**提纲时动手。两条都不接的情况宁可什么都不做，也不要在用户文件里留一段
    半成品（比如被抹成空白的 `## 提纲`）。
    """
    # 没有那一段 → 什么都不写（该走 insert_outline）
    plain = str(tmp_path / "plain.md")
    ensure_node_note(plain, meta={"node_id": "p"}, title="P")
    before = read_text(plain)
    assert replace_outline(plain, ["一"]) is False
    assert read_text(plain) == before

    # 空条目 → 保留上一版，而不是把这一段抹成空白
    path = _note_with_outline(tmp_path)
    keep = read_text(path)
    assert replace_outline(path, []) is False
    assert replace_outline(path, ["", "  "]) is False
    assert read_text(path) == keep
    assert read_outline(path) == ["老口径的一整句话", "老口径的第二条"]

    # 文件不在 → False
    assert replace_outline(str(tmp_path / "missing.md"), ["x"]) is False


# ---- write_user_notes：右栏保存「我的笔记」（走查反馈 ④）----------------


def _note_with_user(tmp_path, note="我自己写的笔记"):
    """一份「什么都有」的笔记文件：提纲 / 讲解记录 / 用户手改过的笔记。

    四段都要在里面 —— 保存笔记时它们**全部**得活下来，缺一段这条测试就漏一种回归。
    """
    path = str(tmp_path / "u.md")
    ensure_node_note(path, meta={"node_id": "n"}, title="A", key_points=["要点一"])
    insert_outline(path, ["有哪些分块方法"])
    append_explanation(path, title="讲解", body="这一轮的正文。")
    write_text(path, read_text(path).replace("程序不会改写这一节", note))
    return path


def _strip_user(text: str) -> str:
    """把「我的笔记」那一段整个摘掉——用来断言「其余字节逐字未变」。

    与 `_strip_outline` 同一个形状：区别只是用户区**必然**是最后一段（摘掉它
    就是截在那个标题处，尾巴为空）。尾巴不空的话说明文件里多出了一段。
    """
    at = text.index(USER_SECTION)
    rest = text[at + len(USER_SECTION):]
    m = re.search(r"^##\s", rest, re.MULTILINE)
    return text[:at] + (rest[m.start():] if m else "")


def test_write_user_notes_swaps_only_that_section(tmp_path):
    """核心不变量:**只动那一段**——提纲、讲解记录、front-matter 逐字保留。"""
    path = _note_with_user(tmp_path)
    before = read_text(path)

    assert write_user_notes(path, "新写的笔记\n\n- 一条") is True
    after = read_text(path)

    assert read_user_notes(path) == "新写的笔记\n\n- 一条"
    assert "我自己写的笔记" not in after
    # 讲解记录与提纲都还在（讲解是只追加的学习资产，程序还要往里写）
    assert "这一轮的正文。" in after
    assert "有哪些分块方法" in after
    assert parse_front_matter(after)["node_id"] == "n"
    # 摘掉用户区之后，前后两份文件必须**逐字相同**
    assert _strip_user(after) == _strip_user(before)


def test_write_user_notes_is_idempotent(tmp_path):
    """保存两次同样的内容 → 文件一模一样（不是「越存越长」）。"""
    path = _note_with_user(tmp_path)
    assert write_user_notes(path, "同一份笔记") is True
    once = read_text(path)
    assert write_user_notes(path, "同一份笔记") is True
    assert read_text(path) == once
    assert read_text(path).count(USER_SECTION) == 1


def test_write_user_notes_keeps_working_when_it_is_the_last_section(tmp_path):
    """用户区是最后一段（后面没有 `^##`）时，保存不能把尾巴吃掉或多出半截。"""
    path = str(tmp_path / "last.md")
    write_text(path, f"# B\n\n{USER_SECTION}\n\n旧的\n")
    assert write_user_notes(path, "新的") is True
    assert read_text(path) == f"# B\n\n{USER_SECTION}\n\n新的\n"
    assert read_user_notes(path) == "新的"


def test_write_user_notes_appends_when_the_section_is_missing(tmp_path):
    """没有那一段 → **追加到末尾**，不插在中间。

    位置是这里唯一的不变量:用户区是 `append_explanation` 的插入锚点（新讲解插在它
    上面），它漂到文件中间，下一段讲解就会跟着落到中间去。
    """
    path = str(tmp_path / "bare.md")
    write_text(path, "# C\n\n## 要点\n\n- 一\n")
    assert write_user_notes(path, "补上的笔记") is True

    text = read_text(path)
    assert read_user_notes(path) == "补上的笔记"
    assert text.index(POINTS_SECTION) < text.index(USER_SECTION)
    assert text.endswith("补上的笔记\n")


def test_write_user_notes_keeps_the_heading_for_empty_text(tmp_path):
    """清空笔记是可预期的事,但**标题行必须留下**——它是 `append_explanation` 的锚点。

    删掉它的话，下一段讲解会走「补一个用户区」的分支，把刚清空的区域又填上默认提示
    ——用户看到的是「我清掉的笔记自己回来了」。
    """
    path = _note_with_user(tmp_path)
    assert write_user_notes(path, "") is True

    text = read_text(path)
    assert heading_index(text, USER_SECTION) >= 0, "清空笔记把标题行一起删了 —— 锚点没了"
    assert read_user_notes(path) == ""
    assert "我自己写的笔记" not in text

    # 锚点还在，于是下一段讲解仍然插在用户区**之前**
    append_explanation(path, title="Q", body="清空之后的新讲解", stamp="2026-09-13 10:00")
    text = read_text(path)
    assert text.index("清空之后的新讲解") < text.index(USER_SECTION)
    assert read_user_notes(path) == "", "新讲解漏进了用户区"


def test_write_user_notes_refuses_when_the_file_is_missing(tmp_path):
    """文件不在 → False 且一个字节也不写（调用方负责先 `rehome_note` 重建骨架）。"""
    missing = str(tmp_path / "nope.md")
    assert write_user_notes(missing, "写不进去") is False
    assert read_text(missing) is None
    assert not os.path.exists(missing)


def test_read_user_notes_returns_empty_without_that_section(tmp_path):
    """没有那一段 / 文件不在 → `""`（读口不抛异常：它是给调用方判空用的）。"""
    bare = str(tmp_path / "bare2.md")
    write_text(bare, "# D\n\n## 要点\n\n- 一\n")
    assert read_user_notes(bare) == ""
    assert read_user_notes(str(tmp_path / "gone.md")) == ""


# ---- render_outline：图谱大纲（第六轮，`report.md` 的正文）----------------


def _node_for_outline(name, **extra):
    return {
        "name": name,
        "summary": f"{name} 的一句话说明",
        "key_points": [f"{name} 要点"],
        "prerequisites": [],
        "order_index": 0,
        **extra,
    }


def test_render_outline_lists_name_summary_keywords_and_prerequisites():
    text = render_outline(
        [
            _node_for_outline("文本切分", order_index=0, key_points=["定长", "递归"]),
            _node_for_outline("向量检索", order_index=1, prerequisites=["文本切分"]),
        ]
    )
    assert text.startswith("## 1. 文本切分\n")
    assert "文本切分 的一句话说明" in text
    assert "关键词：定长、递归" in text
    assert "## 2. 向量检索\n" in text
    assert "前置：文本切分" in text


def test_render_outline_sorts_by_order_index_itself():
    """调用方不排序：排序只在这里做一次（少一处「谁都以为自己该排」的地方）。"""
    text = render_outline(
        [
            _node_for_outline("后学的", order_index=5),
            _node_for_outline("先学的", order_index=1),
        ]
    )
    assert text.index("## 1. 先学的") < text.index("## 2. 后学的")


def test_render_outline_omits_empty_lines():
    """没关键词、没前置的节点不该拖出两个空行——大纲是给人看，也是给 `_excerpt` 用的。"""
    text = render_outline(
        [_node_for_outline("孤点", summary="", key_points=[], prerequisites=[])]
    )
    assert text == "## 1. 孤点\n"


def test_render_outline_survives_junk():
    """节点来自 `build_learning_graph`，但也可能来自别的调用方；不抛异常是硬要求。"""
    assert render_outline([]).startswith("（本次生成没有产出知识点）")
    assert render_outline(None).startswith("（本次生成没有产出知识点）")
    assert render_outline(["不是字典", None]) == "（本次生成没有产出知识点）\n"


def test_render_outline_output_is_stable_for_the_same_graph():
    """纯函数：同一份图谱渲染两次逐字相同（旧版那一步是会随机变空的模型调用）。"""
    nodes = [_node_for_outline("A"), _node_for_outline("B", order_index=1)]
    assert render_outline(nodes) == render_outline(nodes)


# ---- 配置默认值 ----------------------------------------------------------


def test_learning_enabled_by_default():
    """学习图谱**默认开**——这是本阶段唯一一个刻意偏离「opt-in 默认关」的开关。

    Phase 9 把主页换成了图谱主导的界面，那么门面就不该由后端引擎开关决定：
    默认关会让第一次打开的人看到一张「未启用」提示卡，而这个页面本身就是产品。
    注意「默认开」不等于「会花钱」——所有 GET 端点零 LLM 调用，不配 key 也能
    浏览已有图谱，只有 create/generate/chat/mastery 才需要 key。
    """
    s = Settings(_env_file=None)
    assert s.learning_enabled is True
    assert s.learning_db_path == "./data/learning.db"
    assert s.learning_notes_dir == "./data/knowledge"
    assert s.learning_auto_explain is False  # 打开节点不自动发 LLM 请求


def test_learning_env_overrides(monkeypatch):
    # 默认已是 True，所以这里要验的是**关得掉**（.env 里 LEARNING_ENABLED=false 仍能退回
    # 纯研究聊天形态），否则「默认开」就等于「不可关」。
    monkeypatch.setenv("LEARNING_ENABLED", "false")
    monkeypatch.setenv("LEARNING_MAX_NODES", "20")
    monkeypatch.setenv("LEARNING_MASTERY_CONFIDENCE", "0.5")
    s = Settings(_env_file=None)
    assert s.learning_enabled is False
    assert s.learning_max_nodes == 20
    assert s.learning_mastery_confidence == 0.5
