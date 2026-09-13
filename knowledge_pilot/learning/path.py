"""学习路径：prompt 抽取 + **确定性**图构建 + 永不阻断的降级链。

为什么不复用 `kg/extract.py`：那里抽的是「共现关系」——无方向（`RAG -使用-> 向量库`）、
无粒度、无顺序，喂不出「谁必须在谁之前学」。学习路径要的是**前置关系 + 推荐学习顺序**，
所以单独写一版 prompt，但复用它的防御式清洗风格（`parse_json_object` + `_valid_*`）。

`build_learning_graph` 是纯函数：LLM 的脏输出（同名、重复、环、自相矛盾的 order）
全在这里被确定性清洗掉，不靠「祈祷模型听话」。它也**因此可以不联网单测**——这是把
「模型质量」与「产品可用性」解耦的关键：模型抽得差，用户拿到的是一张朴素的图；
模型抽得烂，用户拿到的还是一条线性路径，而不是一个崩溃或一片空白。
"""

import re

from knowledge_pilot.llm.json_utils import parse_json_object

LEARNING_PATH_PROMPT = (
    "你是学习路径规划助手。给定一份研究资料，拆解出学习者应该掌握的**知识点**，"
    "以及它们之间的**前置关系**，输出为 JSON。\n"
    '严格只输出 JSON（不要任何多余文字），格式为：\n'
    '{"nodes": [{"name": "知识点名（2-12 字，不要编号）", "type": "概念|技术|方法|工具", '
    '"summary": "一句话说明", "key_points": ["要点1", "要点2"], '
    '"prerequisites": ["必须先掌握的知识点名"], "order": 1}]}\n'
    "要求：\n"
    "1. 知识点粒度对齐：一个名字对应一个可单独讲解、单独检验的概念，不要过粗也不要过细；\n"
    "2. `prerequisites` 里的名字必须**原样**出现在 nodes 的 name 中，基础知识点留空数组；\n"
    "3. `order` 是你建议的学习顺序（从 1 开始，越小越先学）；\n"
    "4. 不要输出重复知识点，不要臆造资料中不存在的内容。"
)

# 报告里的结构性标题——它们不是知识点，降级解析时要滤掉。
_NON_KNOWLEDGE = re.compile(
    r"^(摘要|概述|引言|前言|背景|结论|总结|结语|参考|参考文献|来源|资料来源|附录|"
    r"abstract|summary|introduction|conclusion|references|sources|appendix)\b",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
# 标题里常见的编号前缀（"1." "1.1" "一、" "（1）"）——剥掉，让知识点名干净。
_NUMBERING = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.、)]?|[一二三四五六七八九十]+[、.)]|（\d+）)\s*")

RELATION = "前置"


async def extract_learning_nodes(llm, text: str) -> list[dict]:
    """让 LLM 抽出知识点；**任何失败返回 []**，由调用方降级（永不抛异常）。"""
    text = (text or "").strip()
    if not text:
        return []
    prompt = [
        {"role": "system", "content": LEARNING_PATH_PROMPT},
        {"role": "user", "content": f"请从以下资料拆解学习路径：\n{text}"},
    ]
    try:
        raw = await llm.complete(prompt, response_format={"type": "json_object"})
    except Exception:
        return []
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return []
    return _valid_nodes(parsed.get("nodes"))


def _valid_nodes(raw) -> list[dict]:
    """逐项清洗：丢掉没有名字的、把字段压成正确类型。"""
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = _clean_name(item.get("name"))
        if not name:
            continue
        key_points = [
            str(p).strip() for p in _as_list(item.get("key_points")) if str(p).strip()
        ]
        prerequisites = []
        for pre in _as_list(item.get("prerequisites")):
            cleaned = _clean_name(pre)
            if cleaned and cleaned != name:
                prerequisites.append(cleaned)
        out.append(
            {
                "name": name,
                "type": str(item.get("type") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "key_points": key_points,
                "prerequisites": prerequisites,
                "order": _as_int(item.get("order")),
            }
        )
    return out


def _clean_name(value) -> str:
    name = " ".join(str(value or "").split())
    return _NUMBERING.sub("", name).strip(" ：:。.")


def _as_list(value) -> list:
    """把模型给的字段压成列表：是列表就用，是标量就包成单元素，空/None 给空列表。

    模型经常把 `["A"]` 写成 `"A"`（尤其是只有一个前置的时候）。直接迭代标量会
    `TypeError: 'int' object is not iterable` —— 而这发生在**解析模型输出**的路径上，
    不能靠「模型不会这么干」来保证。
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _as_int(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_learning_graph(nodes: list[dict], *, max_nodes: int = 12) -> dict:
    """把 LLM 抽出的知识点整理成一张可渲染、可排序、无环的学习图（**纯函数**）。

    依次做四件事，每一步都只依赖上一步的输出（确定性，可单测）：

    1. **归一化 + 同名合并**：按名字去重，`key_points` 取并集、`prerequisites` 取并集；
    2. **截断**：按 LLM 建议的 order 取前 `max_nodes` 个（抽多了会让图糊成一团）；
    3. **建边**：丢掉自环与指向未知名字的前置（模型幻觉出来的依赖不该出现在图里）；
    4. **Kahn 拓扑排序 + 破环 + 迭代算 depth**：
       - 环是真实存在的（A 依赖 B、B 依赖 A），Kahn 会在剩下的环上取不到入度 0 的节点——
         此时按 (LLM order, 名字) 强行放行一个，把环打断，而不是死循环或抛错；
       - **order_index 用拓扑序**（不是 LLM 的 order）：前置一致性优先于模型的主观顺序，
         否则会出现「箭头向下游指、序号却在前」的视觉矛盾。LLM 的 order 只在**同层平局**
         时当 tie-break，用来尊重模型对并列项的偏好；
       - depth 沿拓扑序**迭代**算（`depth = max(前置 depth) + 1`），不用递归——深链
         （比如模型吐出 30 级前置）会让递归撞 `RecursionError`，而迭代不会。

    返回 `{"nodes": [...含 depth/order_index...], "edges": [...]}`。
    """
    merged: dict[str, dict] = {}
    for node in nodes or []:
        # 非 dict 的条目直接丢：这个函数的契约是「脏输出也**绝不抛异常**」，而模型
        # 偶尔会往数组里塞一个 null/字符串（`[{"name": "A"}, "B"]`）。上面那句
        # `node.get` 遇到它们会 `AttributeError`，把整张图连同已经清洗好的节点一起炸掉。
        if not isinstance(node, dict):
            continue
        name = _clean_name(node.get("name"))
        if not name:
            continue
        existing = merged.get(name)
        if existing is None:
            merged[name] = {
                "name": name,
                "type": node.get("type") or "",
                "summary": node.get("summary") or "",
                "key_points": _as_list(node.get("key_points")),
                "prerequisites": _as_list(node.get("prerequisites")),
                "order": _as_int(node.get("order"), len(merged) + 1),
            }
            continue
        # 同名合并：要点/前置取并集并保序去重，其余字段以先到者为准。
        existing["key_points"] = _dedup(existing["key_points"] + _as_list(node.get("key_points")))
        existing["prerequisites"] = _dedup(
            existing["prerequisites"] + _as_list(node.get("prerequisites"))
        )
    if not merged:
        return {"nodes": [], "edges": []}

    kept = sorted(
        merged.values(), key=lambda n: (n["order"] if n["order"] is not None else 10**6, n["name"])
    )[: max(1, max_nodes)]
    names = {n["name"] for n in kept}

    prereqs: dict[str, list[str]] = {}
    for node in kept:
        prereqs[node["name"]] = _dedup(
            [p for p in node["prerequisites"] if p in names and p != node["name"]]
        )

    order = _topological_order(prereqs, kept)
    # 只认「排在前面」的前置。无环时这就是全部前置（拓扑序保证前置在前）；**有环**时
    # 被强制放行的那个节点，其未解前置排在它后面——必须按「不算数」处理，把它当根
    # （depth 0）。否则会读到那个前置的初始值 0，算出一个假的 depth 1，而且真实深度
    # 会整体往上飘一格（环里的每个节点都多算一层）。
    position = {name: index for index, name in enumerate(order)}
    depth: dict[str, int] = {}
    for name in order:
        earlier = [p for p in prereqs[name] if position[p] < position[name]]
        depth[name] = max((depth[p] for p in earlier), default=-1) + 1

    by_name = {n["name"]: n for n in kept}
    out_nodes = [
        {
            "name": name,
            "type": by_name[name]["type"],
            "summary": by_name[name]["summary"],
            "key_points": by_name[name]["key_points"],
            "prerequisites": prereqs[name],
            "depth": depth[name],
            "order_index": index,
        }
        for index, name in enumerate(order)
    ]
    out_edges = [
        {"source": pre, "target": name, "relation": RELATION}
        for name in order
        for pre in prereqs[name]
    ]
    return {"nodes": out_nodes, "edges": out_edges}


def _topological_order(prereqs: dict[str, list[str]], kept: list[dict]) -> list[str]:
    """Kahn 拓扑序，遇环强制放行（按 LLM order + 名字）。返回完整名字序列。"""
    llm_order = {
        n["name"]: (n["order"] if n["order"] is not None else 10**6) for n in kept
    }
    remaining = set(prereqs)
    resolved: set[str] = set()
    order: list[str] = []

    while remaining:
        ready = sorted(
            (n for n in remaining if all(p in resolved for p in prereqs[n])),
            key=lambda n: (llm_order[n], n),
        )
        if not ready:
            # 环：按 (LLM order, 名字) 放行一个，把环打断。绝不死循环。
            forced = min(remaining, key=lambda n: (llm_order[n], n))
            ready = [forced]
        for name in ready:
            remaining.discard(name)
            resolved.add(name)
            order.append(name)
    return order


def nodes_from_headings(report: str, *, fallback_name: str = "") -> list[dict]:
    """降级链第二级：把报告的 `##`/`###` 标题解析成一条**线性学习路径**。

    零 LLM、确定性、**永远成功**——这是「模型挂了也还有东西可看」的保证。标题不是理想
    的知识点（粒度不齐、有「摘要/结论」这种非知识点），但比空白强得多，而且用户能立刻
    开始学；重新生成一次就能换成真正的图谱。

    优先用 `##`（章节）；不足 2 个时退回 `###`（小节）。结构性标题（摘要/结论/来源…）滤掉。
    """
    candidates: list[str] = []
    for hashes, title in _HEADING.findall(report or ""):
        if hashes == "##":
            candidates.append(title)
    if len(candidates) < 2:
        candidates = [title for _, title in _HEADING.findall(report or "")]

    names = _dedup(
        [
            clean
            for clean in (_clean_name(t) for t in candidates)
            if clean and not _NON_KNOWLEDGE.match(clean)
        ]
    )
    if not names:
        # 报告里连一个像样的标题都没有：给一个单节点，用户至少能开始聊。
        names = [(fallback_name or "本主题").strip() or "本主题"]

    return [
        {
            "name": name,
            "type": "章节",
            "summary": "",
            "key_points": [],
            "prerequisites": [names[i - 1]] if i else [],
            "order": i + 1,
        }
        for i, name in enumerate(names)
    ]


def nodes_from_plan(plan: list) -> list[dict]:
    """降级链第二级：把**研究计划**解析成一条线性学习路径（零 LLM、确定性）。

    它是 `nodes_from_headings` 的搭档/替代品：第六轮起学习侧的生成流程不再有报告
    （见 `agent/graph.py::extract_node`），「从报告标题降级」于是失去了输入。计划是
    这一路上**一定存在**的那个东西——planner 先跑，它失败的话整条流程早就失败了。

    每项取 `title`（规划 prompt 里的短标题），没有就用 `question`；`purpose` 当
    一句话说明。`key_points` 刻意留空：计划里的句子是**研究问题**，不是这个知识点
    「要掌握的关键词」，把问句塞进关键词芯片就是在编造一份看起来像抽取结果的清单。

    `type` 用「章节」——与 `nodes_from_headings` 同一个词，表示「这一条是结构性的，
    不是模型抽出来的」。前端类型筛选用的是原样字符串，两种降级路径因此长得一样。

    一个名字都取不到（计划为空或全是空白）→ 返回 `[]`。**这里刻意没有「单节点兜底」**：
    一个只有一个点的图正是用户报的那个故障的样子（看着像成功、什么也学不了），
    所以这一级宁可交白卷，由调用方落 `failed` + 可重试（见 `service.py` 的闸门）。
    """
    nodes: list[dict] = []
    seen: set[str] = set()
    for step in plan or []:
        name = _clean_name(_plan_title(step))
        if not name or name in seen:
            continue
        seen.add(name)
        nodes.append(
            {
                "name": name,
                "type": "章节",
                "summary": (_plan_field(step, "purpose") or _plan_field(step, "question")),
                "key_points": [],
                "prerequisites": [nodes[-1]["name"]] if nodes else [],
                "order": len(nodes) + 1,
            }
        )
    return nodes


def _plan_field(step, key: str) -> str:
    """计划项取字段：只认 dict（planner 的产出），别的一律当空——降级路径不许抛异常。"""
    return str(step.get(key) or "").strip() if isinstance(step, dict) else ""


def _plan_title(step) -> str:
    return _plan_field(step, "title") or _plan_field(step, "question")


def _dedup(items: list[str]) -> list[str]:
    """保序去重（同名合并时用；顺序代表模型的原始偏好，别打乱）。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
