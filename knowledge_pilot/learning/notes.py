"""学习正文的 Markdown 层：命名 / 渲染 / 原子写 / 追加讲解 / 读回自愈。

分工上 SQLite 存**结构与状态**（谁是谁的前置、学到哪了），Markdown 存**正文**——
用户可以直接打开 `data/knowledge/...` 用任何编辑器看和改，不必过一遍我们的 UI。
两侧用 front-matter 里的 `topic_id` / `node_id` 对齐。

文件布局：

    data/knowledge/<topic-slug>-<id6>/
      report.md                 # 该主题的研究总报告
      01_文本切分策略.md         # <order_index+1:02d>_<slug>.md
      02_向量检索.md

每份知识点文件五段：front-matter（镜像 DB 的关键字段）/ `## 提纲` /
`## 要点` / `## 讲解记录`（**只追加不覆写**，保留历史）/ `## 我的笔记`
（**程序永不触碰的用户区**——追加讲解时靠它做锚点，把新内容插在它前面）。

`## 提纲` 是其中唯一**按需插入**的一段（见 `insert_outline`）：建文件时还没有它，
用户第一次打开这个知识点时才由 `learning/outline.py` 生成并插进 `## 要点` 之前。
这样「不生成图谱的人不用为提纲付费」与「提纲属于这个知识点的正文」两件事都成立。

写文件一律**原子写**（同目录 `.tmp` → `os.replace`）：中途崩掉不会留下半截文件
覆盖掉用户已有的笔记。
"""

import os
import re
from datetime import datetime

# Windows 文件名禁用字符 + 控制字符。CJK / 空格 / 中划线都保留（用户看得懂才有意义）。
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留设备名：即便加了扩展名，`CON.md` 依然打不开。
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_SEPARATORS = re.compile(r"[\s_\-]+")

USER_SECTION = "## 我的笔记"
EXPLAIN_SECTION = "## 讲解记录"
POINTS_SECTION = "## 要点"
OUTLINE_SECTION = "## 提纲"

DEFAULT_USER_HINT = "（在这里记笔记——程序不会改写这一节）"


def slugify(text: str, *, max_len: int = 40) -> str:
    """标题 → 文件名安全的 slug（保留中文与空格压缩后的连字符）。

    空结果 / Windows 保留设备名 → 回退一个安全串，绝不返回空文件名。
    """
    cleaned = _UNSAFE.sub("", text or "").strip()
    cleaned = _SEPARATORS.sub("-", cleaned).strip("-.")
    cleaned = cleaned[:max_len].strip("-.")
    if not cleaned or cleaned.lower() in _RESERVED:
        return "untitled"
    return cleaned


def topic_dir_name(slug: str, topic_id: str) -> str:
    """主题目录名：slug + 短 id。

    加短 id 是因为标题会撞车（两个主题都叫「RAG chunking」很常见），而目录一旦撞车
    两个主题的正文就会混在同一个文件夹里。短 id 让目录名仍可读且**稳定**（同一主题
    每次算出的都是同一个目录，不依赖建目录时的现场状态）。
    """
    return f"{slug}-{topic_id[:6]}"


def note_filename(order_index: int, name: str) -> str:
    """`01_文本切分策略.md`——序号在建文件时冻结进文件名。

    序号是**建文件那一刻**的推荐学习顺序；之后重生成图导致顺序变化也**不改文件名**
    （改名会断开用户已经写在文件里的引用，且旧文件会变成孤儿）。当前顺序以
    front-matter 的 `order` 与 DB 为准，文件名只当一个稳定的把手。
    """
    return f"{int(order_index) + 1:02d}_{slugify(name)}.md"


REPORT_FILENAME = "report.md"


def note_path(topic_dir: str, order_index: int, name: str) -> str:
    """知识点正文路径：`<topic_dir>/01_文本切分策略.md`。

    `normpath` 统一分隔符：`.env` 里写 `./data/knowledge` 时 `os.path.join` 会拼出
    `./data/knowledge\\01_x.md` 这种前后反斜杠混用的串——能打开，但会原样存进 DB 并
    显示给用户，看着像 bug。
    """
    return os.path.normpath(os.path.join(topic_dir, note_filename(order_index, name)))


def report_path(topic_dir: str) -> str:
    """该主题的研究总报告路径：`<topic_dir>/report.md`。"""
    return os.path.normpath(os.path.join(topic_dir, REPORT_FILENAME))


def render_front_matter(meta: dict) -> str:
    """front-matter 块（镜像 DB 的关键字段，供外部工具与自愈读回）。"""
    lines = ["---"]
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def parse_front_matter(text: str) -> dict:
    """读回 front-matter（简单 `key: value`，与 store 里「不引入 yaml」的取舍一致）。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def render_node_note(*, meta: dict, title: str, key_points: list[str] | None = None) -> str:
    """渲染一份全新的知识点文件（只用于**首次创建**）。"""
    parts = [render_front_matter(meta), "", f"# {title}", "", POINTS_SECTION]
    points = [p for p in (key_points or []) if str(p).strip()]
    parts.extend(f"- {p}" for p in points)
    if not points:
        parts.append("（本知识点暂无预置要点，可在下方对话中逐步展开）")

    parts += [
        "",
        EXPLAIN_SECTION,
        "",
        "（还没有讲解记录——点开本知识点开始对话后会追加到这里）",
        "",
        USER_SECTION,
        "",
        DEFAULT_USER_HINT,
        "",
    ]
    return "\n".join(parts)


def ensure_node_note(path: str, *, meta: dict, title: str, key_points: list[str] | None = None) -> bool:
    """文件不存在才创建，返回是否真的建了。

    **已存在的文件永不覆写**——它装着用户的讲解记录与手写笔记，那是用户的东西。
    重生成图只会更新 DB 里的结构/顺序；文件名带序号且序号冻结，所以路径稳定，
    内容也就一直归用户所有。用户若删了文件，下次读到时用 DB 重建一个骨架（自愈）。
    """
    if os.path.exists(path):
        return False
    write_text(path, render_node_note(meta=meta, title=title, key_points=key_points))
    return True


def render_outline(nodes: list[dict]) -> str:
    """把**最终图谱**渲染成 Markdown 大纲（`report.md` 的正文）。**纯函数、零 LLM。**

    第六轮起 `report.md` 装的是这个，而不是一份研究报告：写报告那一步在学习侧被删掉了
    （见 `agent/graph.py::extract_node`），而这个文件本身还有用——检视栏能打开它、
    `outline.py::_excerpt` 拿它给按需提纲当上下文。

    好处不只是「少一次长文调用」：渲染是纯函数，于是「文件里有什么」重新变成一个
    **确定性的函数**。原来那份报告会被推理模型吃光预算（`reasoning_content` 与正文
    共用 `max_tokens`），于是同一个动作有时写出 4000 字、有时一个字都没有——而
    「一个字都没有」在下游表现为一张只有一个点的图谱。

    收到的 `nodes` 是 `store.get_graph()` 的形状（含 `order_index` / `key_points` /
    `prerequisites`），按 `order_index` 升序渲染——调用方不排序，排序只在这里做一次。
    """
    ordered = sorted(
        [n for n in (nodes or []) if isinstance(n, dict)],
        key=lambda n: (int(n.get("order_index") or 0), str(n.get("name") or "")),
    )
    parts: list[str] = []
    for index, node in enumerate(ordered, start=1):
        parts += [f"## {index}. {node.get('name') or ''}", ""]
        summary = str(node.get("summary") or "").strip()
        if summary:
            parts += [summary, ""]
        points = [str(p).strip() for p in (node.get("key_points") or []) if str(p).strip()]
        if points:
            parts.append(f"关键词：{'、'.join(points)}")
        prereqs = [str(p).strip() for p in (node.get("prerequisites") or []) if str(p).strip()]
        if prereqs:
            parts.append(f"前置：{'、'.join(prereqs)}")
        if points or prereqs:
            parts.append("")
    if not parts:
        return "（本次生成没有产出知识点）\n"
    return "\n".join(parts).rstrip() + "\n"


def render_report(*, topic_title: str, query: str, report: str, sources: list | None = None) -> str:
    """研究总报告文件（`report.md`）——主题头 + `> 研究问题` + 正文 + 可选来源。

    第六轮起，图谱生成走的那条路给进来的正文是 `render_outline` 的产物（图谱大纲），
    `agent_mode="loop"` 那条路仍是研究报告本身。这里只负责套上文件头/来源，不关心
    正文是谁写的——两条路共用同一个文件格式。
    """
    parts = [f"# {topic_title}", "", f"> 研究问题：{query}", ""]
    parts.append((report or "（本次研究没有产出报告）").rstrip())
    if sources:
        parts += ["", "## 来源", ""]
        for i, source in enumerate(sources, start=1):
            title = source.get("title") or source.get("url") or ""
            url = source.get("url") or ""
            parts.append(f"[{i}] {title} {url}".rstrip())
    return "\n".join(parts) + "\n"


def ensure_topic_dir(notes_dir: str, dir_name: str) -> str:
    """建主题目录并返回其绝对路径。"""
    path = os.path.join(notes_dir, dir_name)
    os.makedirs(path, exist_ok=True)
    return path


def write_text(path: str, text: str) -> str:
    """原子写：同目录临时文件 → `os.replace`（同盘替换是原子的）。

    直接 `open(path, "w")` 在写入中途崩掉会留下截断文件——而这里存的是用户的学习
    记录，截断即丢失，所以值得多写三行。
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def read_text(path: str) -> str | None:
    """读文件；不存在/不可读 → None（调用方据此走自愈重建）。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def append_explanation(path: str, *, title: str, body: str, stamp: str | None = None) -> str:
    """往「讲解记录」段追加一条，**保留**其余所有内容（含用户笔记）。

    新内容插在 `## 我的笔记` **之前**——用户区永远在文件末尾，程序只在它上面生长，
    这样用户手改过的笔记不会被任何一次追加冲掉。
    """
    stamp = stamp or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    block = f"\n### {title}（{stamp}）\n\n{body.rstrip()}\n"
    text = read_text(path)
    if text is None:
        # 文件被外部删掉了：重建一个只剩这段讲解的最小骨架，而不是静默丢弃。
        text = f"# {title}\n\n{EXPLAIN_SECTION}\n\n{USER_SECTION}\n\n{DEFAULT_USER_HINT}\n"

    if EXPLAIN_SECTION not in text:
        text = text.rstrip() + f"\n\n{EXPLAIN_SECTION}\n"
    anchor = text.find(USER_SECTION)
    if anchor < 0:
        # 没有用户区（用户自己删了，或老文件）：补一个，保证下次追加仍有锚点。
        text = text.rstrip() + f"\n{block}\n{USER_SECTION}\n\n{DEFAULT_USER_HINT}\n"
    else:
        text = text[:anchor] + block.lstrip("\n") + "\n" + text[anchor:]
    return write_text(path, text)


# ---- 提纲（按需插入的那一段）------------------------------------------


def heading_index(text: str, heading: str) -> int:
    """`heading` 作为**整行标题**出现的位置；没有 → -1。

    刻意不用 `heading in text`：正文里写一句「参见上面的 ## 提纲」也会命中，
    于是 `insert_outline` 会以为已经插过了，而 `read_outline` 会从后面捞出一堆
    与提纲无关的列表项。标题必须独占一行，这个判据才成立。
    """
    match = re.search(r"^" + re.escape(heading) + r"\s*$", text or "", re.MULTILINE)
    return match.start() if match else -1


def _list_items(section: str) -> list[str]:
    """一段正文里的列表项（`1. xxx` / `- xxx` 都认），去掉空项。"""
    out = []
    for match in re.finditer(r"^\s*(?:\d+[.)]|[-*+])\s+(.*)$", section or "", re.MULTILINE):
        item = match.group(1).strip()
        if item:
            out.append(item)
    return out


def outline_block(items: list[str]) -> str:
    """`## 提纲` 段的完整文本。

    **有序列表**（`1.` / `2.`）而不是 `-`：提纲回答的是「先弄明白什么、再弄明白
    什么」，一组无序的项目符号读不出这条线，而它正是这一段存在的理由。
    """
    lines = [OUTLINE_SECTION, ""]
    lines.extend(f"{i}. {text}" for i, text in enumerate(items, start=1))
    return "\n".join(lines) + "\n"


def read_outline(path: str) -> list[str]:
    """读回 `## 提纲` 的条目；没有这一段 / 文件不在 → `[]`。

    存在的意义是**判重**：已经有提纲的节点再打开时不该再花一次 LLM 调用
    （`learning/service.py::ensure_node_outline` 的第一道闸门）。
    """
    text = read_text(path)
    if text is None:
        return []
    at = heading_index(text, OUTLINE_SECTION)
    if at < 0:
        return []
    rest = text[at + len(OUTLINE_SECTION):]
    end = re.search(r"^##\s", rest, re.MULTILINE)
    return _list_items(rest[: end.start()] if end else rest)


def insert_outline(path: str, items: list[str]) -> bool:
    """把 `## 提纲` **插进**一份已存在的笔记，返回是否真的写了。

    插在**第一个二级标题之前**（正常文件里就是 `## 要点` 之前），于是文件的阅读
    顺序变成「提纲（先学什么）→ 要点（结论）→ 讲解记录 → 我的笔记」。

    三条不变量，每一条都有测试钉着：

    1. **只新增，不改写** —— 除插入点外，其余字节逐字保留（用户手改过的笔记、
       累积的讲解记录都不受影响）；
    2. **幂等** —— 已经有 `## 提纲` 的整行标题就返回 False，一个字节也不动。
       这条同时是「重开节点不重复写」的保证；
    3. **空提纲不落盘** —— `items` 全空时返回 False。宁可不写，也不要在用户文件里
       留一个空的 `## 提纲`（读起来像程序出错了）。

    文件不存在 → False（调用方负责先 `ensure_node_note` / `rehome_note` 重建骨架）。
    """
    text = read_text(path)
    if text is None:
        return False
    if heading_index(text, OUTLINE_SECTION) >= 0:
        return False
    cleaned = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not cleaned:
        return False

    block = outline_block(cleaned)
    first_section = re.search(r"^##\s", text, re.MULTILINE)
    if first_section:
        at = first_section.start()
        return bool(write_text(path, text[:at] + block + "\n" + text[at:]))
    # 一份连二级标题都没有的文件（用户自己删空了）：接到末尾，不留半截。
    return bool(write_text(path, text.rstrip() + "\n\n" + block))


def replace_outline(path: str, items: list[str]) -> bool:
    """把已有的 `## 提纲` 段**整段换掉**，返回是否真的写了。

    `insert_outline` 的对偶：那一条只在**还没有**这一段时动手、从不覆写；这一条只在
    **已经有**这一段时动手。之所以需要它，是走查反馈 ② ——提纲的颗粒度从「一句话」
    改成了「8~14 字的小点」，而旧节点文件里躺着的是老口径的那一版；没有出口的话，
    用户只能自己去文件里删掉那一段再重开节点。

    三条不变量：

    1. **只动那一段** —— 从 `## 提纲` 整行标题到下一个 `^##` 标题之间的字节被换掉，
       文件其余部分（要点 / 讲解记录 / 用户手改过的笔记）逐字保留；
    2. **没有这一段 → False，什么都不写**（该走 `insert_outline`）。两条函数因此
       互不重叠，也都不会有「插进一个已经有的段」这种半吊子状态；
    3. **空条目 → False** —— 生成失败时保留上一版，而不是把用户文件里的这一段抹成
       空白（那种「文件被清掉了一块」比留着旧提纲糟得多）。

    文件不存在 → False。
    """
    text = read_text(path)
    if text is None:
        return False
    at = heading_index(text, OUTLINE_SECTION)
    if at < 0:
        return False
    cleaned = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not cleaned:
        return False

    # 与 `read_outline` 用**同一个**段末判据（下一个 `^##` 标题）——两处若各写各的，
    # 「读到的」和「换掉的」迟早不是同一段：读到的条目会被下面那段的第一行带偏，
    # 或者替换把别人的小节吃掉一截。
    rest = text[at + len(OUTLINE_SECTION):]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    tail = rest[nxt.start():] if nxt else ""
    return bool(write_text(path, text[:at] + outline_block(cleaned) + ("\n" + tail if tail else "")))


# ---- 用户笔记（唯一由用户写、程序只读的那一段）-------------------------


def read_user_notes(path: str) -> str:
    """读回 `## 我的笔记` 的正文（**不含**那行标题，两端空白去掉）；没有 → `""`。

    段末判据与 `write_user_notes` / `read_outline` 用**同一个**（下一个 `^##` 标题）：
    两处各写各的，「读到的」与「换掉的」迟早不是同一段（写回时把下一段吃掉一截）。
    """
    text = read_text(path)
    if text is None:
        return ""
    at = heading_index(text, USER_SECTION)
    if at < 0:
        return ""
    rest = text[at + len(USER_SECTION):]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    return (rest[: nxt.start()] if nxt else rest).strip("\n")


def write_user_notes(path: str, text: str) -> bool:
    """把 `## 我的笔记` 那一段**整段换掉**，返回是否真的写了。

    这是右栏「讲解记录」页的保存口（走查反馈 ④）：用户区是文件里唯一允许改的一段，
    所以这个函数的全部职责就是**只动那一段**。

    四条不变量，每一条都有测试钉着：

    1. **只动那一段** —— front-matter / `## 提纲` / `## 要点` / `## 讲解记录` 逐字
       保留。讲解记录是只追加的学习资产，程序还要往里写；允许整份编辑会让两者互相
       覆盖（哪一边后写谁赢，取决于用户按没按保存）。
    2. **没有这一段 → 追加到文件末尾**，不插在中间。用户的笔记区**永远在最后一行**，
       它是 `append_explanation` 的插入锚点（新讲解插在它上面），位置漂了下一段讲解
       就会落到文件中间。
    3. **空文本也保留标题行** —— 那一行同样是上面的锚点。用户清空笔记是可预期的事，
       但删掉标题会让 `append_explanation` 走「补一个用户区」的分支，把刚清空的区域
       又填上默认提示。
    4. **文件不在 → False**，一个字节也不写（调用方负责先 `rehome_note` 重建骨架，
       同 `ensure_node_outline` 的第三道闸门）。

    **已知边界**：正文里以 `## ` 开头的一行会被当成段的结束（与 `read_user_notes`
    同一判据）。用户随手写个 `## 子标题` 不会丢内容（它连同后面的文字留在原位），
    但也不会再出现在文本框里 —— 所以前端在编辑框下写了一句「别用 `##` 开新行」。
    """
    current = read_text(path)
    if current is None:
        return False
    body = (text or "").strip("\n")
    block = f"{USER_SECTION}\n\n{body}\n" if body else f"{USER_SECTION}\n"

    at = heading_index(current, USER_SECTION)
    if at < 0:
        return bool(write_text(path, current.rstrip() + "\n\n" + block))

    rest = current[at + len(USER_SECTION):]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    tail = rest[nxt.start():] if nxt else ""
    return bool(write_text(path, current[:at] + block + ("\n" + tail if tail else "")))


def update_note_status(path: str, status: str) -> bool:
    """把 front-matter 的 `status` 同步成 DB 里的值；文件不在则跳过（返回 False）。

    文件是正文的「快照」，DB 才是权威；同步失败绝不该影响点亮动作本身。
    """
    text = read_text(path)
    if text is None:
        return False
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end < 0:
        return False
    head, tail = text[3:end], text[end:]
    lines = head.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("status:"):
            lines[i] = f"status: {status}"
            break
    else:
        lines.append(f"status: {status}")
    return bool(write_text(path, "---" + "\n".join(lines) + tail))
