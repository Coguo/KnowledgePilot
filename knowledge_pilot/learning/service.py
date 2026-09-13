"""学习图谱的编排层：研究结果 → 学习图 → 落库 → Markdown 正文。

**不依赖 FastAPI**（也不 import 它），所以这一层可以在纯 pytest 里跑，API 层只负责
把它的产出翻译成 SSE 帧 / JSON。分层理由与 `agent/graph.py` 一致：编排逻辑不该和
web 框架耦合，否则「建图」这件事就只能在起服务之后才测得动。

- `create_topic_shell` —— 建壳，**零 LLM**（用户点「新建主题」要立刻有反馈）；
- `build_graph_from_material` —— **第六轮的主路径**：抽好的知识点（或研究计划）→ 图，
  纯函数、零 LLM，失败时返回空图让调用方落 failed；
- `build_graph_from_report` —— 老路径（报告 → 抽节点），只服务 `agent_mode="loop"`；
- `persist_graph` —— 落库 + 生成 Markdown 骨架 + 写 `report.md`（图谱模式下是大纲）；
- `ensure_node_outline` —— **打开节点时**按需补一份提纲（唯一一次调用发生在用户
  真的要看某个知识点的时候，不是建图的时候）。
"""

from knowledge_pilot.learning import notes, outline
from knowledge_pilot.learning.path import (
    build_learning_graph,
    extract_learning_nodes,
    nodes_from_headings,
    nodes_from_plan,
)

# 降级原因（发在 SSE 状态帧里，让用户知道「这不是模型抽的，是标题凑的」）。
DEGRADED_HEADINGS = "未能从资料中抽取出知识点，已按报告标题生成线性学习路径"
DEGRADED_PLAN = "未能从资料中抽取出知识点，已按研究计划生成线性学习路径"

# 「一条可用知识点都没有」的判据（`build_graph_from_material` 返回它当降级原因，
# 调用方据此**落 failed** 而不是假装成功）。
NOTHING_TO_BUILD = "没有抽取出任何知识点，也没有可用的研究计划"


def create_topic_shell(store, query: str, *, title: str = "") -> dict:
    """建主题壳（`status=empty`），不调 LLM。返回主题 dict。"""
    return store.create_topic(query, title=title)


def build_graph_from_material(
    nodes: list[dict] | None,
    plan: list | None,
    *,
    max_nodes: int,
) -> tuple[dict, str]:
    """**已经抽好的知识点**（或研究计划）→ 学习图。**纯函数、零 LLM。**

    这是第六轮的主路径：报告那一步被删掉了，研究图直接把抽出节点的结果（`NodesEvent`）
    交到这里。与 `build_graph_from_report` 的差别不只是「少一次调用」，而是**失败模式
    变了**：那边是「模型有没有写出正文」（推理模型会把预算吃光，于是随机地一个字都没有），
    这边是「模型有没有吐出 JSON」。

    降级链（与原来同级，只是第二级换了源）：

    1. 有节点 → `build_learning_graph` 清洗成有向无环图；
    2. 没有节点 → 用**研究计划**拼一条线性路径（`nodes_from_plan`）——计划一定存在，
       因为 planner 失败的话整条流程早就失败了；
    3. 计划也空 → 返回**空图** + `NOTHING_TO_BUILD`。

    第 3 级**刻意不给「单节点兜底」**：一个只有一个点的图正是用户报的那个故障的样子
    （看着像成功、什么也学不了）。宁可交白卷，由调用方落 `failed` + 可重试，让上一次
    那张好图留在原地（见 `api/learning.py::_generate_stream` 的闸门）。
    """
    graph = build_learning_graph(nodes or [], max_nodes=max_nodes)
    if graph["nodes"]:
        return graph, ""

    fallback = nodes_from_plan(plan or [])
    graph = build_learning_graph(fallback, max_nodes=max_nodes)
    if graph["nodes"]:
        return graph, DEGRADED_PLAN
    return graph, NOTHING_TO_BUILD


async def build_graph_from_report(
    llm, report: str, *, max_nodes: int, topic_title: str = ""
) -> tuple[dict, str]:
    """研究报告 → 学习图。返回 `(graph, degraded_reason)`；`reason == ""` 表示未降级。

    **第六轮起这条路只服务 `agent_mode="loop"`**（那条研究流程的收尾仍然是写报告，
    没有 `extract` 节点），graph 模式走 `build_graph_from_material`。两边的返回契约
    一样，所以调用方只在「有没有拿到 NodesEvent」上分一次叉。

    降级链：

    1. LLM 抽出知识点 → `build_learning_graph` 清洗成有向无环图；
    2. 抽不到（模型挂了 / 返回非 JSON / 名字全是空的）→ 用报告标题拼一条线性路径；
    3. 报告本身是空的 → **空图** + `NOTHING_TO_BUILD`（调用方落 failed）。

    第 3 级是第六轮加的：原来这里会拿 `topic_title` 造一个单节点图，而「报告一个字都
    没有」恰恰是推理模型吃光预算的典型症状——把它当成功，用户拿到的就是那个「只剩
    一个中心节点」的图。现在它必须变成一次**看得见的失败**。
    """
    if not (report or "").strip():
        return {"nodes": [], "edges": []}, NOTHING_TO_BUILD

    nodes = await extract_learning_nodes(llm, report) if llm is not None else []
    graph = build_learning_graph(nodes, max_nodes=max_nodes)
    if graph["nodes"]:
        return graph, ""

    fallback = nodes_from_headings(report, fallback_name=topic_title)
    return build_learning_graph(fallback, max_nodes=max_nodes), DEGRADED_HEADINGS


def persist_graph(
    store,
    topic_id: str,
    graph: dict,
    *,
    notes_dir: str,
    report: str = "",
    sources: list | None = None,
) -> dict:
    """把图落库并为每个知识点生成 Markdown 骨架，返回统计。

    `report` 是要写进 `report.md` 的**正文**：图谱模式下调用方给的是 `render_outline`
    渲染出来的大纲（`notes.render_outline(graph["nodes"])`），loop 模式下是研究报告本身。
    文件头与来源由 `notes.render_report` 套。空字符串 = 不写这个文件。

    顺序不能反：先 `replace_graph`（拿到稳定 node id / 复用旧身份），再按 **order_index**
    生成文件名——序号在建文件那一刻冻结进文件名，之后顺序变了也不改名（改名会让用户
    已有的引用变孤儿）。

    已存在的 `.md` **不覆写**（`ensure_node_note` 保证），只在落库后把 front-matter 的
    `status` 同步成 DB 里的最新值——文件是快照，DB 才是权威。
    """
    topic = store.get_topic(topic_id)
    if topic is None:
        raise ValueError(f"主题不存在: {topic_id}")

    store.replace_graph(topic_id, nodes=graph["nodes"], edges=graph["edges"])

    topic_dir = notes.ensure_topic_dir(
        notes_dir, notes.topic_dir_name(topic["slug"], topic_id)
    )
    full = store.get_graph(topic_id)
    for node in full["nodes"]:
        path = notes.note_path(topic_dir, node["order_index"], node["name"])
        notes.ensure_node_note(
            path,
            meta=_node_meta(topic_id, node),
            title=node["name"],
            key_points=node.get("key_points") or [],
        )
        notes.update_note_status(path, node["status"])
        store.set_note_path(node["id"], path)

    if report:
        report_path = notes.write_text(
            notes.report_path(topic_dir),
            notes.render_report(
                topic_title=topic["title"], query=topic["query"],
                report=report, sources=sources,
            ),
        )
        store.update_topic(topic_id, report_path=report_path)

    return {
        "nodes": len(full["nodes"]),
        "edges": len(full["edges"]),
        "notes_dir": topic_dir,
    }


def load_node_payload(store, node_id: str) -> dict | None:
    """知识点 + Markdown 正文（**纯读，零 LLM**）。文件缺失时返回空正文而非报错。"""
    node = store.get_node(node_id)
    if node is None:
        return None
    node["body"] = notes.read_text(node.get("note_path") or "") or ""
    return node


def note_file(store, node_id: str, *, notes_dir: str) -> str:
    """拿到某知识点的 Markdown 路径；文件不在就**先重建骨架**，返回路径。

    这是写口（`POST /nodes/{id}/note`）的那道自愈闸门，与 `ensure_node_outline` 闸门 3
    同一个理由：`notes.write_user_notes` 在路径不存在时返回 False 且**不报错**，少了这
    一步用户看到的现象就是「点了保存，文件里却没有」——最糟的一种失败（说成功了）。

    知识点不存在 → `ValueError`（调用方转 404）。
    """
    node = store.get_node(node_id)
    if node is None:
        raise ValueError(f"知识点不存在: {node_id}")
    path = node.get("note_path") or ""
    if notes.read_text(path) is None:
        return rehome_note(store, node_id, notes_dir=notes_dir)
    return path


async def ensure_node_outline(
    store,
    node_id: str,
    *,
    llm,
    notes_dir: str,
    max_items: int = outline.MAX_ITEMS,
    force: bool = False,
) -> dict:
    """打开节点时**按需**补一份提纲，并写进它的 Markdown。返回：

        {"outline": [...], "generated": bool, "stored": bool, "body": "<最新正文>"}

    `body` 一起返回是刻意的：前端拿到后直接 `S.node.body = body` 重画右栏，不需要
    再发一次 GET，也不必在 JS 里复刻一遍「把 `## 提纲` 插在哪里」——那份规则只该有
    一个实现（`notes.insert_outline` / `notes.replace_outline`）。

    三道闸门，顺序不能换：

    1. **已经有提纲 → 直接回读，零 LLM**（这是「重开节点不花钱」的全部实现）；
    2. 生成失败 / 取不出条目 → `[]`，**不写文件**（见 `outline.py` 的取舍）；
    3. 笔记文件不在了（用户删了、或 DB 来自旧版本）→ 先 `rehome_note` 补一份骨架，
       再插提纲。少了这一步，提纲会被写进一个不存在的路径：`insert_outline` 返回
       False，而用户看到的现象是「每次打开都在重新生成，但文件里永远没有」。

    **`force=True` 是给「重新生成」按钮的**（走查反馈 ②：提纲的颗粒度改了，旧节点
    文件里那一段是老口径写的）：跳过闸门 1 重新花钱生成一次，再用
    `replace_outline` **换掉那一段**。与闸门 2 的取舍一致——生成不出条目时
    `replace_outline` 返回 False，文件里的旧提纲原样留着（宁可旧，不可空）。

    `stored=False` 因此有两种含义：这次没写成（文件不在 / 生成失败），以及
    **`force` 下没生成出东西、于是刻意保留了上一版**。前端只把 `outline` 空不空
    当判据，不受影响。
    """
    node = store.get_node(node_id)
    if node is None:
        raise ValueError(f"知识点不存在: {node_id}")

    path = node.get("note_path") or ""
    existing = notes.read_outline(path)
    if existing and not force:
        return {
            "outline": existing, "generated": False, "stored": True,
            "body": notes.read_text(path) or "",
        }

    topic = store.get_topic(node["topic_id"]) or {}
    report = notes.read_text(topic.get("report_path") or "") or ""
    items = await outline.generate_outline(
        llm, node, topic_title=topic.get("title") or "", report=report, max_items=max_items
    )
    if not items:
        return {
            "outline": [], "generated": False, "stored": False,
            "body": notes.read_text(path) or "",
        }

    if notes.read_text(path) is None:
        path = rehome_note(store, node_id, notes_dir=notes_dir)
    # 已经有那一段（`force` 重生成）就走替换：`insert_outline` 见了整行标题就返回
    # False，用它的话「重新生成」会是一次**静默的空操作**——花了钱、文件没变、界面
    # 也看不出来（`body` 没变，前端照旧画旧的）。
    stored = (notes.replace_outline if existing else notes.insert_outline)(path, items)
    return {
        "outline": items, "generated": True, "stored": stored,
        "body": notes.read_text(path) or "",
    }


def rehome_note(store, node_id: str, *, notes_dir: str) -> str:
    """给一个还没有 Markdown 文件的知识点补建文件（读回自愈用），返回路径。

    存在理由：DB 是从更早的版本 / 手工导入来的、或用户删掉了文件——这时打开知识点
    不该看到一片空白，而应重建一个带 DB 记录的骨架。
    """
    node = store.get_node(node_id)
    if node is None:
        raise ValueError(f"知识点不存在: {node_id}")
    topic = store.get_topic(node["topic_id"])
    topic_dir = notes.ensure_topic_dir(
        notes_dir, notes.topic_dir_name(topic["slug"], node["topic_id"])
    )
    path = notes.note_path(topic_dir, node["order_index"], node["name"])
    notes.ensure_node_note(
        path, meta=_node_meta(node["topic_id"], node), title=node["name"],
        key_points=node.get("key_points") or [],
    )
    store.set_note_path(node_id, path)
    return path


def _node_meta(topic_id: str, node: dict) -> dict:
    """front-matter：镜像 DB，供外部编辑器与自愈读回对齐。"""
    prereqs = [p["name"] for p in node.get("prerequisites") or []]
    return {
        "topic_id": topic_id,
        "node_id": node.get("id", ""),
        "order": int(node.get("order_index") or 0) + 1,
        "type": node.get("type") or "",
        "prerequisites": ", ".join(prereqs) if prereqs else "无",
        "status": node.get("status") or "unlearned",
    }
