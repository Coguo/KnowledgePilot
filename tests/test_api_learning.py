"""Phase 9 M4：学习图谱 API（`/api/learning/*`）。

两条主线：

1. **开关语义**：`LEARNING_ENABLED=false`（默认）时全部路由 503，且**不建库不建目录**
   ——Phase 0–8 的行为必须逐字节不变；
2. **读写分离**：只有 create / generate / mastery 可能触发 LLM，**GET 一律零 LLM**。
   这是「重启后直接读回、不重新检索」这条需求在 HTTP 层的落点，所以用**计数假客户端**
   直接断言调用次数不增，而不是靠「读代码觉得应该不会调」。

研究管线本身（graph/loop）已有测试，这里把它换成一个假的异步 runner：本文件要验的是
「拿到报告之后怎么整理、落库、报错」，不是「研究报告怎么产出」。
"""

import json
import os
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from knowledge_pilot.agent.events import (
    DoneEvent,
    NodesEvent,
    PlanEvent,
    StatusEvent,
    TokenEvent,
)
from knowledge_pilot.api import learning as learning_api
from knowledge_pilot.api.main import ChatDeps, app, get_chat_deps
from knowledge_pilot.learning import create_learning_store
from knowledge_pilot.learning import notes as notes_mod
from knowledge_pilot.search.stub import StubSearchProvider

REPORT = (
    "## 文本切分\n\n按固定长度或自然边界把长文档切成块。\n\n"
    "## 向量检索\n\n把查询与分块编码到同一向量空间做近邻搜索。\n\n"
    "## 重排序\n\n用 CrossEncoder 对候选精排。\n"
)

PATH_JSON = json.dumps(
    {
        "nodes": [
            {"name": "文本切分", "type": "技术", "summary": "切块",
             "key_points": ["定长", "递归"], "prerequisites": [], "order": 1},
            {"name": "向量检索", "type": "技术", "summary": "近邻搜索",
             "key_points": ["embedding"], "prerequisites": ["文本切分"], "order": 2},
            {"name": "重排序", "type": "技术", "summary": "精排",
             "key_points": ["CrossEncoder"], "prerequisites": ["向量检索"], "order": 3},
        ]
    },
    ensure_ascii=False,
)
# graph 模式下 runner **直接**交出来的知识点（不经报告），见 `_fake_runner`。
PATH_NODES = json.loads(PATH_JSON)["nodes"]
# 抽取回来的那句话结论（主题摘要读它）。
SUMMARY = "围绕切分、检索与重排序的学习路径"
# 研究计划：抽取一个都没抽到时的备料（降级链第二级）。
PLAN = [
    {"title": "文本切分", "question": "怎么切", "purpose": "打基础"},
    {"title": "向量检索", "question": "怎么检索", "purpose": "接着学"},
    {"title": "重排序", "question": "怎么精排", "purpose": "最后一步"},
]


class _CountingLLM:
    """计数假客户端：本文件最关键的断言（GET 零调用）全靠它。"""

    def __init__(self, reply: str = PATH_JSON) -> None:
        self.model = "fake"
        self.reply = reply
        self.calls = 0
        self.prompts: list = []

    async def complete(self, messages, *, max_tokens=None, response_format=None, extra_body=None):
        self.calls += 1
        self.prompts.append(messages)
        return self.reply


class _ChatLLM(_CountingLLM):
    """既能流式讲解、又能做判定的假客户端（M5 的知识点对话用）。"""

    def __init__(self, *, deltas=("把长文档", "切成可检索的块。"), judge=None):
        super().__init__()
        self.deltas = list(deltas)
        self.judge = judge or json.dumps(
            {"covered": True, "confidence": 0.9, "reason": "讲清了切分粒度"},
            ensure_ascii=False,
        )

    async def stream_complete(self, messages, *, max_tokens=None, response_format=None):
        for delta in self.deltas:
            yield delta

    async def complete(self, messages, *, max_tokens=None, response_format=None, extra_body=None):
        self.calls += 1
        self.prompts.append(messages)
        if response_format:  # json_object → 判定调用
            return self.judge
        return "".join(self.deltas)


def _fake_runner(
    nodes=PATH_NODES,
    *,
    summary: str = SUMMARY,
    plan=PLAN,
    report: str = REPORT,
    tokens: tuple = (),
    raises: BaseException | None = None,
):
    """假研究管线，**镜像真实 runner 的两套收尾契约**。

    - graph 模式（收尾是 `extract`）：一份 `NodesEvent` + 一句 `DoneEvent`，
      **没有 token 帧**（JSON 不流式吐给用户）；
    - loop 模式（收尾写报告）：`tokens` 里的逐字帧 + `DoneEvent(report)`。

    默认两样都给，两个模式各取自己那一份——这样同一份假件在两种 `agent_mode` 下都成立，
    测试只需在**要验哪一条路**时才把另一条路的参数改掉（比如闸门那条用
    `nodes=[], plan=None`）。

    `nodes=[]` + `plan=[...]` = 抽取失败但有计划（降级链第二级）；
    `nodes=[]` + `plan=None` = 什么都交不出来（闸门 → 落 failed）。
    """
    if nodes is None:
        nodes = PATH_NODES
    async def _run(*args, **kwargs):
        if raises is not None:
            raise raises
        yield StatusEvent(message="研究阶段：综合报告")
        if plan:
            yield PlanEvent(plan=list(plan))
        for delta in tokens:
            yield TokenEvent(delta)
        yield NodesEvent(nodes=list(nodes), summary=summary)
        yield DoneEvent(content=report)

    return _run


@pytest.fixture
def env(tmp_path, monkeypatch):
    """打开学习图谱，把库与正文目录都指到 tmp_path。"""
    monkeypatch.setattr(learning_api.settings, "learning_enabled", True)
    monkeypatch.setattr(
        learning_api.settings, "learning_db_path", str(tmp_path / "learning.db")
    )
    monkeypatch.setattr(
        learning_api.settings, "learning_notes_dir", str(tmp_path / "knowledge")
    )
    monkeypatch.setattr(learning_api.settings, "agent_mode", "graph")
    yield tmp_path
    app.dependency_overrides.pop(get_chat_deps, None)


def _patch_runner(monkeypatch, runner):
    """替换研究管线（graph 与 loop 两个入口都换，免得受 agent_mode 影响）。"""
    monkeypatch.setattr(learning_api, "run_research_graph", runner)
    monkeypatch.setattr(learning_api, "run_research", runner)


def _override_deps(llm):
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider()
    )
    return llm


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _frames(frames):
    """SSE 文本行 → 结构化帧（`[DONE]` 保留成字符串，方便断言收尾）。"""
    out = []
    for line in frames:
        if not line.startswith("data: "):
            continue
        data = line[6:]
        out.append("[DONE]" if data == "[DONE]" else json.loads(data))
    return out


def _typed(frames, kind):
    """按类型挑帧（`[DONE]` 是字符串，不是事件对象，得先滤掉）。"""
    return [f for f in frames if isinstance(f, dict) and f["type"] == kind]


async def _read_sse(client, url, body=None):
    async with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        return _frames([line async for line in resp.aiter_lines()])


async def _chat(client, node_id, message="什么是切分？"):
    return await _read_sse(client, f"/api/learning/nodes/{node_id}/chat",
                           body={"message": message})


async def _generate(client, topic_id):
    return await _read_sse(client, f"/api/learning/topics/{topic_id}/generate")


# ---- 开关闸门 ---------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/learning/topics", None),
        ("POST", "/api/learning/topics", {"query": "x"}),
        ("GET", "/api/learning/topics/abc", None),
        ("DELETE", "/api/learning/topics/abc", None),
        ("POST", "/api/learning/topics/abc/generate", None),
        ("GET", "/api/learning/nodes/abc", None),
        ("GET", "/api/learning/nodes/abc/messages", None),
        ("POST", "/api/learning/nodes/abc/chat", {"message": "hi"}),
        ("POST", "/api/learning/nodes/abc/mastery", {"mastered": True}),
        ("POST", "/api/learning/nodes/abc/outline", None),
    ],
)
async def test_all_routes_503_when_disabled(monkeypatch, method, path, body):
    """默认关时必须整体 503——包括依赖注入都没走到（否则会先报「缺 key」的 500）。"""
    monkeypatch.setattr(learning_api.settings, "learning_enabled", False)
    _override_deps(_CountingLLM())  # 即使 key/依赖可用，开关关着也不该放行
    async with await _client() as client:
        resp = await client.request(method, path, json=body)
    assert resp.status_code == 503
    assert "LEARNING_ENABLED" in resp.json()["detail"]


async def test_disabled_creates_no_db_or_notes_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(learning_api.settings, "learning_enabled", False)
    monkeypatch.setattr(
        learning_api.settings, "learning_db_path", str(tmp_path / "learning.db")
    )
    async with await _client() as client:
        await client.get("/api/learning/topics")
    assert not (tmp_path / "learning.db").exists()


# ---- 建壳（不调 LLM） -------------------------------------------------


async def test_create_topic_returns_shell_without_llm(env):
    llm = _override_deps(_CountingLLM())
    async with await _client() as client:
        resp = await client.post("/api/learning/topics", json={"query": "RAG chunking"})
    assert resp.status_code == 200
    topic = resp.json()
    assert topic["status"] == "empty"
    assert topic["progress"]["total"] == 0
    assert llm.calls == 0  # 建壳是纯落库


async def test_create_topic_rejects_empty_query(env):
    _override_deps(_CountingLLM())
    async with await _client() as client:
        resp = await client.post("/api/learning/topics", json={"query": "   "})
    assert resp.status_code == 400


async def test_topics_list_is_newest_first(env):
    _override_deps(_CountingLLM())
    async with await _client() as client:
        a = (await client.post("/api/learning/topics", json={"query": "A"})).json()
        b = (await client.post("/api/learning/topics", json={"query": "B"})).json()
        listed = (await client.get("/api/learning/topics")).json()["topics"]
    assert [t["id"] for t in listed] == [b["id"], a["id"]]


# ---- 生成（SSE） ------------------------------------------------------


async def test_generate_streams_nodes_then_the_outline(env, monkeypatch):
    """第六轮：收尾交回来的是**知识点**（不是长报告），大纲是渲染出来的、最后一帧到达。"""
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        frames = await _generate(client, topic["id"])
        graph = await _graph(client, topic["id"])

    extracted = _typed(frames, "nodes")
    assert len(extracted) == 1
    assert extracted[0]["count"] == 3  # 只报个数，不把节点塞进 SSE（与 graph_ready 同规矩）
    assert extracted[0]["summary"] == SUMMARY

    # 大纲**一次性**到达，不是逐字长文：`#gen-body` 里仍有内容可看，但它是个渲染产物
    outlines = [f["content"] for f in _typed(frames, "token")]
    assert len(outlines) == 1
    assert outlines[0].startswith("## 1. 文本切分")
    assert "关键词：定长、递归" in outlines[0]
    assert "前置：文本切分" in outlines[0]

    ready = _typed(frames, "graph_ready")
    assert len(ready) == 1
    assert ready[0]["nodes"] == 3
    assert ready[0]["edges"] == 2
    assert ready[0]["degraded"] is False
    assert frames[-1] == "[DONE]"
    assert not any("降级" in f["message"] for f in _typed(frames, "status"))
    assert llm.calls == 0  # 收尾不再写报告，于是这条路上一次模型调用也没有
    # 大纲先到、图随后：用户看见它时磁盘上已经落好了同一个文件
    assert frames.index(_typed(frames, "token")[0]) < frames.index(ready[0])

    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索", "重排序"]
    assert [n["depth"] for n in graph["nodes"]] == [0, 1, 2]
    assert graph["topic"]["status"] == "ready"
    assert graph["topic"]["summary"] == SUMMARY  # 摘要用那句一句话结论，不再从报告里猜


async def _graph(client, topic_id):
    resp = await client.get(f"/api/learning/topics/{topic_id}")
    assert resp.status_code == 200
    return resp.json()


async def test_generate_writes_markdown_for_report_and_every_node(env, monkeypatch):
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        graph = await _graph(client, topic["id"])

    files = {p.name for p in Path(env / "knowledge").rglob("*.md")}
    assert "report.md" in files
    assert files == {"report.md", "01_文本切分.md", "02_向量检索.md", "03_重排序.md"}

    # report.md 里装的是**图谱大纲**（每个知识点带关键词），不是那份研究报告——
    # 判据用报告特有的句子：它一旦出现在文件里，就说明旧路又回来了。
    report_md = next(Path(env / "knowledge").rglob("report.md")).read_text(encoding="utf-8")
    assert "## 1. 文本切分" in report_md
    assert "关键词：定长、递归" in report_md
    assert "按固定长度或自然边界" not in report_md

    # 每个知识点都记下了自己的正文路径，且 front-matter 与 DB 对齐
    for node in graph["nodes"]:
        assert node["note_path"]
        text = Path(node["note_path"]).read_text(encoding="utf-8")
        meta = notes_mod.parse_front_matter(text)
        assert meta["node_id"] == node["id"]
        assert meta["topic_id"] == topic["id"]
        assert meta["status"] == "unlearned"


async def test_generate_degrades_to_the_plan_when_nothing_was_extracted(env, monkeypatch):
    """一个知识点都没抽到 → 用**研究计划**拼线性路径，并**告知用户**这是降级结果。"""
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner(nodes=[], plan=PLAN))
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        frames = await _generate(client, topic["id"])
        graph = await _graph(client, topic["id"])

    assert any("计划" in f["message"] for f in _typed(frames, "status"))
    ready = _typed(frames, "graph_ready")[0]
    assert ready["degraded"] is True and ready["nodes"] == 3
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索", "重排序"]
    # 降级出来的节点没有关键词（来源是计划，不是资料），但顺序与前置仍然成立
    assert all(n["key_points"] == [] for n in graph["nodes"])
    assert graph["topic"]["status"] == "ready"  # 降级不算失败


async def test_generate_with_nothing_to_build_keeps_the_previous_graph(env, monkeypatch):
    """既没抽到知识点、也没有研究计划 → `failed` + 可重试，**上一次的好图原样留着**。

    这是用户报的「第二次生成只剩一个中心点」的落点：那时兜底是「造一个单节点图」，
    于是失败被伪装成成功、旧图被覆盖。现在宁可交白卷也不交出那张图。
    """
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        before = await _graph(client, topic["id"])
        assert len(before["nodes"]) == 3

        _patch_runner(monkeypatch, _fake_runner(nodes=[], plan=None))
        frames = await _generate(client, topic["id"])
        after = await _graph(client, topic["id"])

        assert frames[-1] == "[DONE]"
        errors = _typed(frames, "error")
        assert len(errors) == 1 and "知识点" in errors[0]["message"]
        assert not _typed(frames, "graph_ready")
        assert after["topic"]["status"] == "failed"
        # 图与正文都没被动过（`persist_graph` 一次都没调用）
        assert [n["id"] for n in after["nodes"]] == [n["id"] for n in before["nodes"]]
        for node in after["nodes"]:
            assert Path(node["note_path"]).exists()

        # 可重试：换成正常的 runner，同一个主题还能再生成
        _patch_runner(monkeypatch, _fake_runner())
        assert _typed(await _generate(client, topic["id"]), "graph_ready")


async def test_loop_mode_still_builds_the_graph_from_the_report(env, monkeypatch):
    """`agent_mode="loop"` 那条路一个字都没改：报告逐字到、报告再抽路径。"""
    monkeypatch.setattr(learning_api.settings, "agent_mode", "loop")
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner(tokens=("## 文本切分\n", "（报告正文）")))
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        frames = await _generate(client, topic["id"])
        graph = await _graph(client, topic["id"])

    assert [f["content"] for f in _typed(frames, "token")] == [
        "## 文本切分\n",
        "（报告正文）",
    ]
    assert llm.calls == 1  # 报告 → 路径抽取那一次（graph 模式没有这一次）
    ready = _typed(frames, "graph_ready")[0]
    assert ready["nodes"] == 3 and ready["degraded"] is False
    assert [n["name"] for n in graph["nodes"]] == ["文本切分", "向量检索", "重排序"]
    assert graph["topic"]["status"] == "ready"
    # 报告原样落进 report.md（不是大纲）
    report_md = next(Path(env / "knowledge").rglob("report.md")).read_text(encoding="utf-8")
    assert "按固定长度或自然边界" in report_md


async def test_generate_failure_marks_topic_failed_and_stays_retryable(env, monkeypatch):
    """研究阶段抛错 → error 帧 + 主题落 failed（而不是留一个假装有图的 ready）。"""
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner(raises=RuntimeError("上游 500")))
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        frames = await _generate(client, topic["id"])
        graph = await _graph(client, topic["id"])

    assert frames[-1] == "[DONE]"
    errors = _typed(frames, "error")
    assert len(errors) == 1 and "上游 500" in errors[0]["message"]
    assert not _typed(frames, "graph_ready")
    assert graph["topic"]["status"] == "failed"
    assert "上游 500" in graph["topic"]["error"]
    assert graph["nodes"] == []
    assert llm.calls == 0  # 报告都没拿到，不该去抽路径

    # 失败后可重试：换成能跑的 runner，同一个主题能生成成功
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        frames = await _generate(client, topic["id"])
    assert _typed(frames, "graph_ready")


def test_topic_summary_prefers_prose_over_the_first_heading():
    """报告第一行几乎总是第一个知识点的标题，拿它当主题摘要等于没说。"""
    assert learning_api._lead("## 文本切分\n\n把长文档切成可检索的块。") == "把长文档切成可检索的块。"
    assert learning_api._lead("## 只有标题没有正文") == "只有标题没有正文"
    assert learning_api._lead("") == ""
    assert learning_api._lead("正文在最前面。\n\n## 后面才有标题") == "正文在最前面。"


def test_topic_summary_is_truncated():
    assert len(learning_api._lead("字" * 500)) == 100


async def test_generate_404s_for_unknown_topic(env):
    _override_deps(_CountingLLM())
    async with await _client() as client:
        resp = await client.post("/api/learning/topics/nope/generate")
    assert resp.status_code == 404


async def test_get_topic_404s_for_unknown_topic(env):
    _override_deps(_CountingLLM())
    async with await _client() as client:
        assert (await client.get("/api/learning/topics/nope")).status_code == 404


# ---- 读端点零 LLM（「重启不重新检索」的落点） -------------------------


async def test_get_endpoints_never_call_the_llm(env, monkeypatch):
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        after_generate = llm.calls

        node_id = (await _graph(client, topic["id"]))["nodes"][0]["id"]
        # 反复读：列表 / 整图 / 知识点正文 / 对话历史
        for _ in range(3):
            await client.get("/api/learning/topics")
            await _graph(client, topic["id"])
            assert (await client.get(f"/api/learning/nodes/{node_id}")).status_code == 200
            assert (
                await client.get(f"/api/learning/nodes/{node_id}/messages")
            ).status_code == 200

    assert llm.calls == after_generate  # 一次都没多——没有检索、没有判定


async def test_node_payload_includes_body_from_markdown(env, monkeypatch):
    """打开知识点读到的是**磁盘上的正文**，且零 LLM（不自动开讲）。"""
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node_id = (await _graph(client, topic["id"]))["nodes"][0]["id"]
        before = llm.calls
        node = (await client.get(f"/api/learning/nodes/{node_id}")).json()

    assert node["name"] == "文本切分"
    assert "# 文本切分" in node["body"]
    assert "定长" in node["body"]  # 预置要点在正文里
    assert notes_mod.USER_SECTION in node["body"]
    assert llm.calls == before


async def test_messages_start_empty_after_generate(env, monkeypatch):
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node_id = (await _graph(client, topic["id"]))["nodes"][0]["id"]
        resp = await client.get(f"/api/learning/nodes/{node_id}/messages")
    assert resp.json()["messages"] == []


# ---- 点亮（用户确认） -------------------------------------------------


async def test_mastery_endpoint_sets_and_revokes(env, monkeypatch):
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node = (await _graph(client, topic["id"]))["nodes"][0]
        before = llm.calls

        on = await client.post(
            f"/api/learning/nodes/{node['id']}/mastery", json={"mastered": True}
        )
        assert on.json()["status"] == "mastered"
        assert (await _graph(client, topic["id"]))["topic"]["progress"]["mastered"] == 1

        off = await client.post(
            f"/api/learning/nodes/{node['id']}/mastery", json={"mastered": False}
        )
        assert off.json()["status"] == "unlearned"
        assert (await _graph(client, topic["id"]))["topic"]["progress"]["mastered"] == 0

    # Markdown 的 front-matter 跟着 DB 走
    assert notes_mod.parse_front_matter(
        Path(node["note_path"]).read_text(encoding="utf-8")
    )["status"] == "unlearned"
    assert llm.calls == before  # 点亮纯状态转移，不问模型


async def test_mastery_404s_for_unknown_node(env):
    _override_deps(_CountingLLM())
    async with await _client() as client:
        resp = await client.post(
            "/api/learning/nodes/nope/mastery", json={"mastered": True}
        )
    assert resp.status_code == 404


# ---- 保存「我的笔记」（走查反馈 ④）------------------------------------


async def test_save_note_swaps_only_the_user_section(env, monkeypatch):
    """走完整链路：只换 `## 我的笔记`，讲解记录与提纲逐字还在。"""
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node = (await _graph(client, topic["id"]))["nodes"][0]
        before = Path(node["note_path"]).read_text(encoding="utf-8")
        calls = llm.calls

        resp = await client.post(
            f"/api/learning/nodes/{node['id']}/note", json={"note": "我的新笔记"}
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["saved"] is True
        assert payload["node_id"] == node["id"]
        # 返回的是**改完之后**的完整正文：前端直接 S.node.body = body 重画，
        # 不必在 JS 里复刻一遍「章节插在哪」的规则（那份规则只该有一个实现）
        assert notes_mod.read_user_notes(node["note_path"]) == "我的新笔记"
        assert payload["body"] == Path(node["note_path"]).read_text(encoding="utf-8")

    after = Path(node["note_path"]).read_text(encoding="utf-8")
    assert "我的新笔记" in after
    # 其余四段的**标题**都还在（正文由下面那条逐字比较兜住）
    assert notes_mod.EXPLAIN_SECTION in after and notes_mod.POINTS_SECTION in after
    assert notes_mod.parse_front_matter(after)["node_id"] == node["id"]
    # 除用户区外逐字未变（含那份讲解记录）
    assert _strip_user_section(after) == _strip_user_section(before)
    assert llm.calls == calls  # 保存笔记**零 LLM**


def _strip_user_section(text: str) -> str:
    """摘掉 `## 我的笔记` 那一段——用来断言「其余字节逐字未变」。"""
    at = text.index(notes_mod.USER_SECTION)
    rest = text[at + len(notes_mod.USER_SECTION):]
    m = re.search(r"^##\s", rest, re.MULTILINE)
    return text[:at] + (rest[m.start():] if m else "")


async def test_save_note_needs_no_api_key(env, monkeypatch):
    """它是**写口但零 LLM**：与 `/mastery` 一样不带 `ChatDeps`（见路由 docstring）。

    判据不能是「调用成功」——本机 `.env` 里就有 key，依赖解析得成，于是「加了 deps」
    和「没加 deps」看起来一模一样（这条测试的第一版正是这么写的，变异验证时它**没有**
    变红）。所以这里把依赖提供者换成一个**必定抛错**的函数：真正的判据是
    「它根本没被解析」（FastAPI 只为签名里出现的依赖调用它）。
    """
    boom = []

    def _boom():
        boom.append(1)
        raise AssertionError("保存笔记不该解析 ChatDeps —— 它零 LLM，不需要 API key")

    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node = (await _graph(client, topic["id"]))["nodes"][0]

        app.dependency_overrides[get_chat_deps] = _boom
        resp = await client.post(
            f"/api/learning/nodes/{node['id']}/note", json={"note": "没有 key 也要能存"}
        )
    assert resp.status_code == 200
    assert resp.json()["saved"] is True
    assert boom == [], "保存笔记解析了 ChatDeps —— 那它就需要 API key了"


async def test_save_note_empty_text_keeps_the_anchor(env, monkeypatch):
    """清空笔记 → 200，且 `## 我的笔记` 那一行还在（它是下次追加讲解的锚点）。"""
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node = (await _graph(client, topic["id"]))["nodes"][0]
        resp = await client.post(f"/api/learning/nodes/{node['id']}/note", json={"note": ""})

    assert resp.status_code == 200
    assert notes_mod.read_user_notes(node["note_path"]) == ""
    assert notes_mod.heading_index(resp.json()["body"], notes_mod.USER_SECTION) >= 0


async def test_save_note_missing_body_defaults_to_empty(env, monkeypatch):
    """不带 `note` 字段 = 空笔记（老前端 / 手搓请求不会 422）。"""
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node = (await _graph(client, topic["id"]))["nodes"][0]
        resp = await client.post(f"/api/learning/nodes/{node['id']}/note", json={})
    assert resp.status_code == 200


async def test_save_note_rejects_an_oversized_payload(env, monkeypatch):
    """粘错东西（整份 report.md）时宁可 422，也不要一次写进用户的知识点文件。"""
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node = (await _graph(client, topic["id"]))["nodes"][0]
        before = Path(node["note_path"]).read_text(encoding="utf-8")
        resp = await client.post(
            f"/api/learning/nodes/{node['id']}/note", json={"note": "x" * 100_001}
        )
    assert resp.status_code == 422
    assert Path(node["note_path"]).read_text(encoding="utf-8") == before


async def test_save_note_404s_for_unknown_node(env):
    _override_deps(_CountingLLM())
    async with await _client() as client:
        resp = await client.post("/api/learning/nodes/nope/note", json={"note": "x"})
    assert resp.status_code == 404


async def test_save_note_rebuilds_a_deleted_note_file(env, monkeypatch):
    """文件被外部删了 → **先重建骨架再写**。

    少了这道闸门 `write_user_notes` 会返回 False 而**不报错**，用户看到的现象是
    「点了保存，文件里却没有」——最糟的一种失败（说成功了）。
    """
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        node = (await _graph(client, topic["id"]))["nodes"][0]
        os.remove(node["note_path"])

        resp = await client.post(
            f"/api/learning/nodes/{node['id']}/note", json={"note": "重建之后写进去的"}
        )
        assert resp.status_code == 200
        assert resp.json()["saved"] is True
        # 路径没漂：还是 DB 里记着的那个（`rehome_note` 会 set_note_path）
        assert (await _graph(client, topic["id"]))["nodes"][0]["note_path"] == node["note_path"]

    assert os.path.exists(node["note_path"])
    assert notes_mod.read_user_notes(node["note_path"]) == "重建之后写进去的"


# ---- 知识点对话与推荐（M5） -------------------------------------------


async def _generate_and_pick_first_node(client, **kw):
    topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
    await _generate(client, topic["id"])
    graph = await _graph(client, topic["id"])
    return topic, graph["nodes"][0]


async def test_chat_streams_explanation_then_recommends(env, monkeypatch):
    llm = _override_deps(_ChatLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic, node = await _generate_and_pick_first_node(client)
        frames = await _chat(client, node["id"])

        assert [f["content"] for f in _typed(frames, "token")] == ["把长文档", "切成可检索的块。"]
        recommend = _typed(frames, "recommend")
        assert len(recommend) == 1
        assert recommend[0]["reason"] == "讲清了切分粒度"
        assert recommend[0]["node_id"] == node["id"]
        assert _typed(frames, "done")[0]["content"] == "把长文档切成可检索的块。"
        assert frames[-1] == "[DONE]"

        # 推荐不等于掌握：等用户确认
        assert (await _graph(client, topic["id"]))["nodes"][0]["status"] == "recommended"
        msgs = (await client.get(f"/api/learning/nodes/{node['id']}/messages")).json()
    assert [m["role"] for m in msgs["messages"]] == ["user", "assistant"]


async def test_chat_refresh_recovers_the_conversation(env, monkeypatch):
    """刷新页面靠 GET messages 恢复对话——纯读，不问模型。"""
    llm = _override_deps(_ChatLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        await _chat(client, node["id"])
        before = llm.calls

        recovered = (await client.get(f"/api/learning/nodes/{node['id']}/messages")).json()
        node_payload = (await client.get(f"/api/learning/nodes/{node['id']}")).json()

    assert [m["content"] for m in recovered["messages"]] == [
        "什么是切分？", "把长文档切成可检索的块。",
    ]
    assert "把长文档切成可检索的块。" in node_payload["body"]  # 也进了 Markdown
    assert llm.calls == before


async def test_second_chat_turn_does_not_judge_again(env, monkeypatch):
    """已推荐过就不再重复判定（不唠叨、不重复花调用）。"""
    llm = _override_deps(_ChatLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        await _chat(client, node["id"])
        after_first = llm.calls

        frames = await _chat(client, node["id"])

    assert llm.calls == after_first  # 没有新增判定调用
    assert not _typed(frames, "recommend")
    assert _typed(frames, "token")


async def test_chat_rejects_empty_message(env, monkeypatch):
    _override_deps(_ChatLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        resp = await client.post(
            f"/api/learning/nodes/{node['id']}/chat", json={"message": "   "}
        )
    assert resp.status_code == 400


async def test_chat_404s_for_unknown_node(env):
    _override_deps(_ChatLLM())
    async with await _client() as client:
        resp = await client.post("/api/learning/nodes/nope/chat", json={"message": "hi"})
    assert resp.status_code == 404


async def test_chat_reports_llm_failure_as_an_error_frame(env, monkeypatch):
    """讲解中途挂了要有明确报错，而不是静默截断。"""
    llm = _override_deps(_ChatLLM())

    async def _boom(messages, *, max_tokens=None, response_format=None):
        raise RuntimeError("上游 502")
        yield  # pragma: no cover — 让它是异步生成器

    monkeypatch.setattr(llm, "stream_complete", _boom)
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        frames = await _chat(client, node["id"])

    assert frames[-1] == "[DONE]"
    errors = _typed(frames, "error")
    assert len(errors) == 1 and "上游 502" in errors[0]["message"]


async def test_chat_with_an_empty_explanation_errors_without_persisting_it(env, monkeypatch):
    """模型一个字都没讲（推理吃光预算）→ 一帧 `error`，**不留空气泡**（第六轮）。

    从前这里是照常落库的：界面上一句空气泡、Markdown 里一条空标题的「讲解记录」，
    而用户不知道发生了什么。
    """
    llm = _override_deps(_ChatLLM(deltas=("", "")))
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        before = Path(node["note_path"]).read_text(encoding="utf-8")
        frames = await _chat(client, node["id"])
        msgs = (await client.get(f"/api/learning/nodes/{node['id']}/messages")).json()

    assert frames[-1] == "[DONE]"
    errors = _typed(frames, "error")
    assert len(errors) == 1 and "没有返回任何内容" in errors[0]["message"]
    assert [m["role"] for m in msgs["messages"]] == ["user"]  # 空回答没落库
    # 正文**一个字节都没动**（`## 讲解记录` 这个空标题本来就在骨架里，不是这次留下的）
    assert Path(node["note_path"]).read_text(encoding="utf-8") == before
    assert llm.calls == 0  # 也没白烧一次判定


# ---- 提纲：打开节点时按需生成（走查反馈 ④） ---------------------------

OUTLINE_ITEMS = ["说清分块粒度为什么会改变检索召回", "比较定长切分与递归切分", "看它在流水线里的位置"]


class _PathAndOutlineLLM(_CountingLLM):
    """路径抽取回 `PATH_JSON`，提纲回 `OUTLINE_JSON`。

    两条调用**都**走 `json_object` 模式，所以没法按 `response_format` 分流
    （`_ChatLLM` 就是那么分的）；这里按 prompt 内容分——提纲那条的 system prompt
    里写着「提纲」二字。
    """

    async def complete(self, messages, *, max_tokens=None, response_format=None, extra_body=None):
        self.calls += 1
        self.prompts.append(messages)
        blob = json.dumps(messages, ensure_ascii=False)
        return json.dumps({"outline": OUTLINE_ITEMS}, ensure_ascii=False) \
            if "提纲" in blob else PATH_JSON


async def _outline(client, node_id):
    return await client.post(f"/api/learning/nodes/{node_id}/outline")


async def test_outline_is_generated_on_demand_once_and_written_into_the_note(env, monkeypatch):
    """一次 LLM、落进 Markdown、之后永远回读——「按需」的全部含义。"""
    llm = _override_deps(_PathAndOutlineLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        before = llm.calls

        # 建图时**不会**生成提纲（那是「按需」的反面：为多数人永远不点开的节点付费）
        text = Path(node["note_path"]).read_text(encoding="utf-8")
        assert notes_mod.OUTLINE_SECTION not in text
        assert llm.calls == before

        resp = await _outline(client, node["id"])
        assert resp.status_code == 200
        data = resp.json()
        assert data["outline"] == OUTLINE_ITEMS
        assert data["generated"] is True and data["stored"] is True
        assert llm.calls == before + 1
        # 响应里带着**最新正文**：前端直接换上即可，不必再发一次 GET
        assert notes_mod.OUTLINE_SECTION in data["body"]

        # 再打开一次（前端每次打开都会判一次）→ 回读，零 LLM
        again = (await _outline(client, node["id"])).json()
        assert again["outline"] == OUTLINE_ITEMS
        assert again["generated"] is False
        assert llm.calls == before + 1

        # 读写分离不被这条路由破坏：纯读端点依旧一次模型也不问
        await client.get(f"/api/learning/nodes/{node['id']}")
        assert llm.calls == before + 1

    text = Path(node["note_path"]).read_text(encoding="utf-8")
    assert text.index(notes_mod.OUTLINE_SECTION) < text.index(notes_mod.POINTS_SECTION)
    assert f"1. {OUTLINE_ITEMS[0]}" in text


async def test_outline_only_adds_a_section_never_rewrites_the_note(env, monkeypatch):
    """用户手写的笔记、已经累积的讲解记录都不动——走的是完整链路，判据与 notes 层一致。"""
    _override_deps(_PathAndOutlineLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        path = Path(node["note_path"])
        original = path.read_text(encoding="utf-8").replace(
            "程序不会改写这一节", "我自己的理解：分块要顺着语义边界走"
        )
        path.write_text(original, encoding="utf-8")

        await _outline(client, node["id"])

    after = path.read_text(encoding="utf-8")
    assert "我自己的理解：分块要顺着语义边界走" in after
    start = after.index(notes_mod.OUTLINE_SECTION)
    end = after.index(notes_mod.POINTS_SECTION)
    assert after[:start] + after[end:] == original


class _TwoVersionOutlineLLM(_PathAndOutlineLLM):
    """提纲**每次调用换一版**（第一次老口径的长句，第二次小点）。

    序列化的假客户端是这条用例的全部要点：如果 `force` 只是把同一份提纲又写回文件
    一次，断言就会看着「文件里确实有提纲」而通过 —— 那样测出来的是「没崩」，
    不是「换掉了」。
    """

    def __init__(self, versions) -> None:
        super().__init__()
        self.versions = list(versions)
        self.n = 0

    async def complete(self, messages, *, max_tokens=None, response_format=None, extra_body=None):
        blob = json.dumps(messages, ensure_ascii=False)
        # 建图那条路径抽取（`PATH_JSON`）照旧 —— 只有提纲这条换序列。
        if "提纲" not in blob:
            return await super().complete(
            messages, max_tokens=max_tokens, response_format=response_format, extra_body=extra_body
        )
        self.calls += 1
        self.prompts.append(messages)
        items = self.versions[min(self.n, len(self.versions) - 1)]
        self.n += 1
        return json.dumps({"outline": items}, ensure_ascii=False)


FORCE_ITEMS = ["有哪些分块方法", "各自的切分规则", "会遇到什么问题"]


async def test_outline_force_regenerates_and_replaces_the_old_section(env, monkeypatch):
    """`{"force": true}` = 花钱重生成一次，并把文件里那一段**换掉**（走查反馈 ②）。

    没有这条路径的话，老节点文件里躺着的老口径提纲永远换不掉：闸门 1（已有就回读）
    会一直命中，而 `insert_outline` 见了整行标题就返回 False —— 用户点「重新生成」
    会是一次**花了钱、文件没变、界面也看不出来**的空操作。
    """
    llm = _override_deps(_TwoVersionOutlineLLM([OUTLINE_ITEMS, FORCE_ITEMS]))
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        path = Path(node["note_path"])
        text = path.read_text(encoding="utf-8").replace(
            "程序不会改写这一节", "我自己写的一笔"
        )
        path.write_text(text, encoding="utf-8")

        assert (await _outline(client, node["id"])).json()["outline"] == OUTLINE_ITEMS
        before = llm.calls

        forced = await client.post(
            f"/api/learning/nodes/{node['id']}/outline", json={"force": True}
        )
        assert forced.status_code == 200
        data = forced.json()
        assert data["outline"] == FORCE_ITEMS
        assert data["generated"] is True and data["stored"] is True
        assert llm.calls == before + 1, "force 没有真的重新调用模型"
        # 响应里带的是**换过之后**的正文，前端直接换上就能看见新的一列
        assert f"1. {FORCE_ITEMS[0]}" in data["body"]

        # 之后又回到「回读、零调用」的老路上（force 不是一次性的开关）
        again = (await _outline(client, node["id"])).json()
        assert again["outline"] == FORCE_ITEMS
        assert again["generated"] is False
        assert llm.calls == before + 1

    after = path.read_text(encoding="utf-8")
    assert OUTLINE_ITEMS[0] not in after, "旧的那一版还留在文件里"
    assert "我自己写的一笔" in after, "换提纲把用户的笔记一起吃掉了"
    assert after.index(notes_mod.OUTLINE_SECTION) < after.index(notes_mod.POINTS_SECTION)


async def test_outline_force_failure_keeps_the_previous_version(env, monkeypatch):
    """重生成失败时**保留上一版**——宁可旧，不可空。

    这与「不留半成品」是同一条取舍的两面：那一条说的是「别写坏」，这一条说的是
    「别把已经写在文件里的东西抹掉」。用户点了一次没成的重试，结果文件里那一段
    变成空白，比什么都没发生糟得多。
    """
    llm = _override_deps(_TwoVersionOutlineLLM([OUTLINE_ITEMS, []]))
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        await _outline(client, node["id"])
        before = llm.calls

        data = (await client.post(
            f"/api/learning/nodes/{node['id']}/outline", json={"force": True}
        )).json()
        assert data["outline"] == [] and data["stored"] is False
        assert llm.calls == before + 1, "force 应当真的试过一次"

    text = Path(node["note_path"]).read_text(encoding="utf-8")
    assert f"1. {OUTLINE_ITEMS[0]}" in text, "上一版被抹掉了"
    assert notes_mod.read_outline(node["note_path"]) == OUTLINE_ITEMS


async def test_outline_without_a_body_behaves_exactly_as_before(env, monkeypatch):
    """老客户端（不带 body）与 `{"force": false}` 都必须还是「已有就回读」。

    这条是给前端留的退路：`force` 走的是同一条路由，一旦它变成了默认行为，每一次
    打开节点都会重新烧一次模型调用 —— 而界面上完全看不出来。
    """
    llm = _override_deps(_TwoVersionOutlineLLM([FORCE_ITEMS, OUTLINE_ITEMS]))
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        await client.post(f"/api/learning/nodes/{node['id']}/outline")  # 不带 body
        after_first = llm.calls

        explicit = await client.post(
            f"/api/learning/nodes/{node['id']}/outline", json={"force": False}
        )
        assert explicit.json()["outline"] == FORCE_ITEMS
        assert explicit.json()["generated"] is False
        assert llm.calls == after_first, "force=false 也去重新生成了"


async def test_outline_failure_writes_nothing_and_stays_retryable(env, monkeypatch):
    """生成失败 → 一个字节都不写，并且下次打开会**真的重试**（不是永远卡在失败上）。"""
    llm = _override_deps(_CountingLLM("模型今天不想输出 JSON"))
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        before = llm.calls

        data = (await _outline(client, node["id"])).json()
        assert data["outline"] == []
        assert data["generated"] is False and data["stored"] is False
        assert llm.calls == before + 1

        await _outline(client, node["id"])
        assert llm.calls == before + 2  # 没写坏 → 还愿意再问一次

    assert notes_mod.OUTLINE_SECTION not in Path(node["note_path"]).read_text(encoding="utf-8")


async def test_outline_rebuilds_a_deleted_note_file(env, monkeypatch):
    """正文文件被外部删掉时先补骨架再插提纲。

    少了这一步，提纲会被写进一个不存在的路径：接口每次都返回「生成了」，而文件里
    永远没有——用户看到的现象是「每次打开都在转圈」。
    """
    _override_deps(_PathAndOutlineLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        _, node = await _generate_and_pick_first_node(client)
        path = Path(node["note_path"])
        path.unlink()

        data = (await _outline(client, node["id"])).json()
        assert data["generated"] is True and data["stored"] is True

    text = path.read_text(encoding="utf-8")
    assert notes_mod.OUTLINE_SECTION in text
    assert notes_mod.USER_SECTION in text  # 重建的是完整骨架，不是只有提纲的残文件


async def test_outline_404s_for_unknown_node(env):
    _override_deps(_CountingLLM())
    async with await _client() as client:
        resp = await _outline(client, "nope")
    assert resp.status_code == 404


# ---- 删除与「重启不重新检索」 -----------------------------------------


async def test_delete_topic_keeps_markdown_but_drops_graph(env, monkeypatch):
    _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        resp = await client.delete(f"/api/learning/topics/{topic['id']}")
        after = await client.get(f"/api/learning/topics/{topic['id']}")

    assert resp.json()["notes_kept"]
    assert after.status_code == 404
    assert list(Path(env / "knowledge").rglob("*.md"))  # 学习资产还在磁盘上


async def test_graph_survives_a_process_restart_without_any_llm_call(env, monkeypatch):
    """「重启后保留、不需重新检索」的直接证明。

    generate 之后**新建一个 store 实例**（等价于进程重启后新开一个连接）读回来，
    图和正文都必须一致——而且整个过程 LLM 调用数一次都不增。
    """
    llm = _override_deps(_CountingLLM())
    _patch_runner(monkeypatch, _fake_runner())
    async with await _client() as client:
        topic = (await client.post("/api/learning/topics", json={"query": "RAG"})).json()
        await _generate(client, topic["id"])
        live = await _graph(client, topic["id"])
        calls_before = llm.calls

    # 模拟重启：换一个全新连接读同一个 db 文件
    store = create_learning_store(learning_api.settings.learning_db_path)
    try:
        reread = store.get_graph(topic["id"])
    finally:
        store.close()

    assert reread["topic"]["status"] == "ready"
    assert [n["id"] for n in reread["nodes"]] == [n["id"] for n in live["nodes"]]
    assert [n["depth"] for n in reread["nodes"]] == [0, 1, 2]
    assert llm.calls == calls_before

    # 正文文件也还在读得回来
    for node in reread["nodes"]:
        assert Path(node["note_path"]).exists()
