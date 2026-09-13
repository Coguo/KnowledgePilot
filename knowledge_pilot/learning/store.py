"""学习图谱存储：SQLite（stdlib sqlite3）持久化主题 / 知识点 / 前置关系 / 学习状态 / 对话。

与 `memory/store.py` 的分工：Memory 存「研究过什么」，回答的是检索复用问题；这里存
「学到哪了」，回答的是学习进度问题——两张库、两套生命周期，不互相污染。

纯标准库实现，不依赖 `[rag]` extra。连接**每次现开现关**（对齐 `open_read_store` 的
结论）：不在进程里常驻 db 句柄，父进程随时可以删/重建文件而不踩 Windows PermissionError，
也让「重启后读到的就是磁盘上最新的」。单次打开 + 建表的开销是微秒级。

## 为什么没有 `_SCHEMA_READY` 表级缓存

计划里提过用模块级缓存跳过重复 DDL。实现时**刻意没做**：`CREATE TABLE IF NOT EXISTS`
本身就是幂等的，而缓存会引入一个真实故障——db 文件被删除后在同一路径重建时，缓存的
「已建表」标记会让新文件一张表都没有，之后所有查询以 "no such table" 炸掉，且症状
取决于进程内是否开过别的库。DDL 走一遍是微秒级，换这个隐患不划算。
"""

import json
import os
import sqlite3
import threading
from datetime import datetime
from uuid import uuid4

from knowledge_pilot.learning.notes import slugify

# 学习状态机：unlearned（未学）→ recommended（系统推荐已掌握，待用户确认）→ mastered（已掌握）。
# 用户可撤销（dismiss 回 recommended→unlearned、撤销掌握 mastered→unlearned）。
NODE_STATUSES = ("unlearned", "recommended", "mastered")

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "unlearned": {"recommended", "mastered"},
    "recommended": {"mastered", "unlearned"},
    "mastered": {"unlearned"},
}

TOPIC_STATUSES = ("empty", "generating", "ready", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    slug TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'empty',   -- empty|generating|ready|failed
    error TEXT NOT NULL DEFAULT '',
    report_path TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    key_points TEXT NOT NULL DEFAULT '[]',  -- JSON 字符串数组
    depth INTEGER NOT NULL DEFAULT 0,       -- 由前置关系推出的层级（0 = 最先学）
    order_index INTEGER NOT NULL DEFAULT 0, -- 推荐学习顺序（拓扑序）
    note_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (topic_id, name)
);

CREATE TABLE IF NOT EXISTS edges (
    topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,  -- 前置
    target_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,  -- 后继
    relation TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_id, target_id)
);

CREATE TABLE IF NOT EXISTS node_state (
    node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'unlearned',
    recommend_reason TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    recommended_at TEXT NOT NULL DEFAULT '',
    mastered_at TEXT NOT NULL DEFAULT '',
    chat_turns INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                     -- user|assistant
    content TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',     -- JSON（RAG 命中的来源，可为空）
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_topic ON nodes(topic_id, order_index);
CREATE INDEX IF NOT EXISTS idx_messages_node ON messages(node_id);
CREATE INDEX IF NOT EXISTS idx_edges_topic ON edges(topic_id);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(raw: str, fallback):
    try:
        value = json.loads(raw or "null")
    except (ValueError, TypeError):
        return fallback
    return fallback if value is None else value


class LearningStore:
    """主题 / 知识点 / 关系 / 学习状态 / 对话的 SQLite 存储。

    所有方法同步 + 加锁（uvicorn 单事件循环 + 可能的线程池调用）；写入走显式事务，
    级联删除靠 FK（每条连接都要单独开 `PRAGMA foreign_keys`，sqlite 的默认是关的）。
    """

    def __init__(self, db_path: str) -> None:
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 级联删除是 per-connection 的开关，不在这开就等于没开。
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- 主题 -----------------------------------------------------------

    def create_topic(self, query: str, *, title: str = "") -> dict:
        """建一个主题壳（status=empty），**不调 LLM**。

        建壳与生成刻意分离：用户点「新建主题」应当立刻返回一个可点的条目，而不是
        卡在一个几十秒的请求里；生成失败也还有个壳能重试。
        """
        topic_id = uuid4().hex
        stamp = _now()
        title = (title or query).strip()
        with self._lock:
            self._conn.execute(
                "INSERT INTO topics (id, title, query, slug, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'empty', ?, ?)",
                (topic_id, title, query, slugify(title), stamp, stamp),
            )
            self._conn.commit()
        return self.get_topic(topic_id)  # type: ignore[return-value]

    def get_topic(self, topic_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
        if row is None:
            return None
        topic = dict(row)
        topic["progress"] = self._progress(topic_id)
        return topic

    def list_topics(self) -> list[dict]:
        """全部主题（新→旧），每条带进度统计。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM topics ORDER BY rowid DESC"
            ).fetchall()
        out = []
        for row in rows:
            topic = dict(row)
            topic["progress"] = self._progress(topic["id"])
            out.append(topic)
        return out

    def update_topic(
        self,
        topic_id: str,
        *,
        status: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        error: str | None = None,
        report_path: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """按需更新主题字段（只更新显式传入的）。status 受白名单校验。"""
        if status is not None and status not in TOPIC_STATUSES:
            raise ValueError(f"未知主题状态: {status!r}（合法：{', '.join(TOPIC_STATUSES)}）")
        fields: dict[str, object] = {"updated_at": _now()}
        for key, value in (
            ("status", status),
            ("title", title),
            ("summary", summary),
            ("error", error),
            ("report_path", report_path),
            ("run_id", run_id),
        ):
            if value is not None:
                fields[key] = value
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE topics SET {assignments} WHERE id = ?",
                (*fields.values(), topic_id),
            )
            self._conn.commit()

    def delete_topic(self, topic_id: str, *, delete_notes: bool = False) -> list[str]:
        """删除主题及其图 / 状态 / 对话，返回被删掉的 Markdown 路径。

        `delete_notes=False`（默认）保留磁盘上的 Markdown——讲解记录是用户的学习资产，
        清库不该顺手把它删掉；要连文件一起删是显式选择。删除走**显式事务删子表**，
        FK 级联只作第二道防线（不依赖它是为了在 `PRAGMA` 意外缺失时也不留孤儿行）。
        """
        paths = [
            row["note_path"]
            for row in self._conn.execute(
                "SELECT note_path FROM nodes WHERE topic_id = ? AND note_path != ''",
                (topic_id,),
            ).fetchall()
        ]
        with self._lock:
            with self._conn:  # 事务：任一步失败整体回滚
                self._conn.execute(
                    "DELETE FROM messages WHERE node_id IN"
                    " (SELECT id FROM nodes WHERE topic_id = ?)",
                    (topic_id,),
                )
                self._conn.execute(
                    "DELETE FROM node_state WHERE node_id IN"
                    " (SELECT id FROM nodes WHERE topic_id = ?)",
                    (topic_id,),
                )
                self._conn.execute("DELETE FROM edges WHERE topic_id = ?", (topic_id,))
                self._conn.execute("DELETE FROM nodes WHERE topic_id = ?", (topic_id,))
                self._conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        return paths

    # ---- 图 -------------------------------------------------------------

    def replace_graph(
        self,
        topic_id: str,
        *,
        nodes: list[dict],
        edges: list[dict],
    ) -> dict:
        """整体替换主题的图，按 `name` **复用既有节点身份**。

        复用：`node.id`、`node_state`（点亮状态 / 对话轮次）、`note_path`、`created_at`。
        更新：type / summary / key_points / depth / order_index（重生成后这些就是新的）。
        消失：新图里不再出现的知识点，会连同它的状态与对话一起删掉（那个知识点已经不存在了）。
        保留身份的实际意义 = **重新生成不丢学习进度**，用户不必因为模型这次少抽了一个
        实体而重学一遍。

        nodes 每项：name / type / summary / key_points / depth / order_index（+ 可选 note_path）。
        edges 每项：source（前置名）/ target（后继名）/ relation，按名字引用。
        """
        stamp = _now()
        with self._lock:
            with self._conn:
                old = {
                    row["name"]: row
                    for row in self._conn.execute(
                        "SELECT id, name, note_path, created_at FROM nodes WHERE topic_id = ?",
                        (topic_id,),
                    ).fetchall()
                }
                keep = {n["name"] for n in nodes}
                stale = [row["id"] for name, row in old.items() if name not in keep]
                if stale:
                    self._conn.executemany(
                        "DELETE FROM nodes WHERE id = ?", [(x,) for x in stale]
                    )
                # 先清边：节点 id 复用时，旧边可能指向已被删除的节点。
                self._conn.execute("DELETE FROM edges WHERE topic_id = ?", (topic_id,))

                reused = 0
                for node in nodes:
                    name = node["name"]
                    slug = node.get("slug") or slugify(name)
                    key_points = json.dumps(
                        node.get("key_points") or [], ensure_ascii=False
                    )
                    if name in old:
                        reused += 1
                        self._conn.execute(
                            "UPDATE nodes SET slug=?, type=?, summary=?, key_points=?,"
                            " depth=?, order_index=?, note_path=?"
                            " WHERE id=?",
                            (
                                slug,
                                node.get("type") or "",
                                node.get("summary") or "",
                                key_points,
                                int(node.get("depth") or 0),
                                int(node.get("order_index") or 0),
                                node.get("note_path") or old[name]["note_path"],
                                old[name]["id"],
                            ),
                        )
                    else:
                        node_id = uuid4().hex
                        self._conn.execute(
                            "INSERT INTO nodes (id, topic_id, name, slug, type, summary,"
                            " key_points, depth, order_index, note_path, created_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                node_id,
                                topic_id,
                                name,
                                slug,
                                node.get("type") or "",
                                node.get("summary") or "",
                                key_points,
                                int(node.get("depth") or 0),
                                int(node.get("order_index") or 0),
                                node.get("note_path") or "",
                                stamp,
                            ),
                        )
                        self._conn.execute(
                            "INSERT INTO node_state (node_id) VALUES (?)", (node_id,)
                        )

                ids = {
                    row["name"]: row["id"]
                    for row in self._conn.execute(
                        "SELECT id, name FROM nodes WHERE topic_id = ?", (topic_id,)
                    ).fetchall()
                }
                written = 0
                for edge in edges:
                    source = ids.get(edge["source"])
                    target = ids.get(edge["target"])
                    if source is None or target is None or source == target:
                        continue  # 指向未知节点/自环：调用方已清洗，这里只兜底
                    self._conn.execute(
                        "INSERT OR IGNORE INTO edges (topic_id, source_id, target_id, relation)"
                        " VALUES (?, ?, ?, ?)",
                        (topic_id, source, target, edge.get("relation") or ""),
                    )
                    written += 1
        return {"nodes": len(nodes), "edges": written, "reused": reused}

    def get_graph(self, topic_id: str) -> dict | None:
        """整图：topic + nodes（含学习状态）+ edges。纯读，零 LLM。"""
        topic = self.get_topic(topic_id)
        if topic is None:
            return None
        with self._lock:
            node_rows = self._conn.execute(
                "SELECT n.*, COALESCE(s.status, 'unlearned') AS status,"
                " COALESCE(s.recommend_reason, '') AS recommend_reason,"
                " COALESCE(s.confidence, 0) AS confidence,"
                " COALESCE(s.recommended_at, '') AS recommended_at,"
                " COALESCE(s.mastered_at, '') AS mastered_at,"
                " COALESCE(s.chat_turns, 0) AS chat_turns"
                " FROM nodes n LEFT JOIN node_state s ON s.node_id = n.id"
                " WHERE n.topic_id = ? ORDER BY n.order_index, n.rowid",
                (topic_id,),
            ).fetchall()
            edge_rows = self._conn.execute(
                "SELECT source_id, target_id, relation FROM edges WHERE topic_id = ?"
                " ORDER BY rowid",
                (topic_id,),
            ).fetchall()
        nodes = []
        for row in node_rows:
            node = dict(row)
            node["key_points"] = _json(node.get("key_points"), [])
            nodes.append(node)
        return {
            "topic": topic,
            "nodes": nodes,
            "edges": [dict(r) for r in edge_rows],
            "order": [n["id"] for n in nodes],
        }

    def get_node(self, node_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT n.*, t.title AS topic_title, COALESCE(s.status, 'unlearned') AS status,"
                " COALESCE(s.recommend_reason, '') AS recommend_reason,"
                " COALESCE(s.confidence, 0) AS confidence,"
                " COALESCE(s.recommended_at, '') AS recommended_at,"
                " COALESCE(s.mastered_at, '') AS mastered_at,"
                " COALESCE(s.chat_turns, 0) AS chat_turns"
                " FROM nodes n"
                " JOIN topics t ON t.id = n.topic_id"
                " LEFT JOIN node_state s ON s.node_id = n.id"
                " WHERE n.id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        node = dict(row)
        node["key_points"] = _json(node.get("key_points"), [])
        node["prerequisites"] = self.prerequisites_of(node_id)
        return node

    def prerequisites_of(self, node_id: str) -> list[dict]:
        """直接前置知识点（{id, name} 列表，按 order_index）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT n.id, n.name FROM edges e JOIN nodes n ON n.id = e.source_id"
                " WHERE e.target_id = ? ORDER BY n.order_index",
                (node_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_note_path(self, node_id: str, note_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET note_path = ? WHERE id = ?", (note_path, node_id)
            )
            self._conn.commit()

    # ---- 学习状态 -------------------------------------------------------

    def set_node_status(
        self, node_id: str, status: str, *, reason: str = "", confidence: float = 0.0
    ) -> str:
        """转移学习状态，返回新状态。

        - 非法状态值 / 非法转移 → ValueError（白名单，不做「猜用户想干什么」）；
        - 同状态 → 幂等空操作（重复点「确认掌握」不该报错）；
        - **永不自动降级**：这里的降级只由用户显式动作触发，判定逻辑在
          `learning/session.py`，它只发 recommended / 不动。
        """
        if status not in NODE_STATUSES:
            raise ValueError(f"未知学习状态: {status!r}（合法：{', '.join(NODE_STATUSES)}）")
        current = self._state_row(node_id)
        if current is None:
            raise ValueError(f"知识点不存在: {node_id}")
        if status == current["status"]:
            return status
        if status not in _ALLOWED_TRANSITIONS[current["status"]]:
            raise ValueError(
                f"非法状态转移: {current['status']} → {status}"
                f"（允许：{', '.join(sorted(_ALLOWED_TRANSITIONS[current['status']])) or '无'}）"
            )

        stamp = _now()
        with self._lock:
            if status == "recommended":
                self._conn.execute(
                    "UPDATE node_state SET status=?, recommend_reason=?, confidence=?,"
                    " recommended_at=? WHERE node_id=?",
                    (status, reason, float(confidence), stamp, node_id),
                )
            elif status == "mastered":
                self._conn.execute(
                    "UPDATE node_state SET status=?, mastered_at=? WHERE node_id=?",
                    (status, stamp, node_id),
                )
            else:  # unlearned：撤销——推荐理由/时间戳一并清掉，不留幽灵状态
                self._conn.execute(
                    "UPDATE node_state SET status='unlearned', recommend_reason='',"
                    " confidence=0, recommended_at='', mastered_at='' WHERE node_id=?",
                    (node_id,),
                )
            self._conn.commit()
        return status

    def get_node_state(self, node_id: str) -> dict | None:
        row = self._state_row(node_id)
        return dict(row) if row is not None else None

    def bump_chat_turns(self, node_id: str) -> int:
        """对话轮次 +1，返回新值（判定成本闸门用它判断「聊够没有」）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE node_state SET chat_turns = chat_turns + 1 WHERE node_id = ?",
                (node_id,),
            )
            self._conn.commit()
        row = self._state_row(node_id)
        return int(row["chat_turns"]) if row is not None else 0

    # ---- 对话 -----------------------------------------------------------

    def add_message(
        self, node_id: str, role: str, content: str, *, sources: list | None = None
    ) -> dict:
        """落一条对话消息，返回该消息 dict（重启后由 list_messages 读回）。"""
        message_id = uuid4().hex
        created_at = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id, node_id, role, content, sources, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    node_id,
                    role,
                    content,
                    json.dumps(sources or [], ensure_ascii=False),
                    created_at,
                ),
            )
            self._conn.commit()
        return {
            "id": message_id,
            "node_id": node_id,
            "role": role,
            "content": content,
            "sources": list(sources or []),
            "created_at": created_at,
        }

    def list_messages(self, node_id: str) -> list[dict]:
        """某知识点的全部对话（旧→新）。纯读，零 LLM。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE node_id = ? ORDER BY rowid",
                (node_id,),
            ).fetchall()
        out = []
        for row in rows:
            message = dict(row)
            message["sources"] = _json(message.get("sources"), [])
            out.append(message)
        return out

    # ---- 内部 -----------------------------------------------------------

    def _progress(self, topic_id: str) -> dict:
        with self._lock:
            (total,) = self._conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE topic_id = ?", (topic_id,)
            ).fetchone()
            rows = self._conn.execute(
                "SELECT COALESCE(s.status, 'unlearned') AS status, COUNT(*) AS n"
                " FROM nodes n LEFT JOIN node_state s ON s.node_id = n.id"
                " WHERE n.topic_id = ? GROUP BY status",
                (topic_id,),
            ).fetchall()
        counts = {row["status"]: row["n"] for row in rows}
        mastered = counts.get("mastered", 0)
        return {
            "total": total,
            "mastered": mastered,
            "recommended": counts.get("recommended", 0),
            "unlearned": counts.get("unlearned", 0),
            # 0 个知识点的空主题算 0%，而不是「全部完成」。
            "percent": round(mastered * 100 / total) if total else 0,
        }

    def _state_row(self, node_id: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM node_state WHERE node_id = ?", (node_id,)
            ).fetchone()

    # ---- 生命周期 -------------------------------------------------------

    def close(self) -> None:
        """释放连接（幂等；数据已持久化到磁盘）。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def open_learning_store(db_path: str) -> LearningStore:
    """新建一个学习存储实例（调用方负责 close()）。

    与 `memory/store.open_read_store` 同约定：**每次请求现开现关**，不缓存进程级单例。
    """
    if not db_path:
        raise ValueError("缺少学习存储路径（db_path 不能为空）")
    return LearningStore(db_path)
