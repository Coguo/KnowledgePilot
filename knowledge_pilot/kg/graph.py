"""纯 stdlib 内存知识图谱存储（Phase 5，零依赖）。

回答规格 §8：RAG 给原始文本证据，KG 给结构化关系信息。
图是「每次研究任务的内存对象」——evidence → LLM 抽实体关系 → GraphStore →
按查询关键词匹配实体 → BFS 展开 → 注入综合报告 prompt。核心版不做跨任务积累、
不做持久化、不做 query_knowledge_graph 工具（后续可扩展）。
"""

import re

_CJK_RANGE = "一-鿿぀-ヿ가-힯"
_CJK_RUN = re.compile(f"[{_CJK_RANGE}]+")
_LATIN_WORD = re.compile(r"[A-Za-z0-9_]+")

# 关键词匹配的实体命中上限（防通用词把整图拉进 BFS）。
KG_MAX_MATCHED_ENTITIES = 10


def tokenize(text: str) -> list[str]:
    """拉丁/数字整词保留 + 中文双字重叠切分（镜像 memory/store.py，独立实现避免模块耦合）。"""
    tokens: list[str] = []
    for match in _LATIN_WORD.finditer(text):
        tokens.append(match.group(0).lower())
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _normalize(name: str) -> str:
    """节点名/关系名归一化：去首尾空白 + 拉丁小写（中文不受影响）。"""
    return (name or "").strip().lower()


def _token_matches(q: str, n: str) -> bool:
    """分词匹配：q==n 恒命中；子串匹配仅限双字符以上，避免单字符误报（"a" in "rag"）。"""
    if q == n:
        return True
    if len(q) < 2 or len(n) < 2:
        return False
    return q in n or n in q


class GraphStore:
    """纯 dict 邻接知识图谱（内存，按研究任务独立实例）。"""

    def __init__(self) -> None:
        self._nodes: dict[str, str] = {}  # name(归一化) → type
        self._triples: set[tuple[str, str, str]] = set()  # (source, relation, target)

    # ---- 写 -------------------------------------------------------------

    def add_entities(self, entities: list[dict]) -> None:
        """批量加入实体；跳过空名，名称归一化（strip + 拉丁小写），重复自动去重。"""
        for ent in entities or []:
            if not isinstance(ent, dict):
                continue
            name = _normalize(ent.get("name"))
            if not name:
                continue
            self._nodes[name] = str(ent.get("type") or "")

    def add_relations(self, relations: list[dict]) -> None:
        """批量加入关系；端点不存在则自动建节点（type 为空串）；跳过空端点/空关系名。"""
        for rel in relations or []:
            if not isinstance(rel, dict):
                continue
            source = _normalize(rel.get("source"))
            target = _normalize(rel.get("target"))
            rel_name = _normalize(rel.get("relation"))
            if not source or not target or not rel_name:
                continue
            self._nodes.setdefault(source, "")
            self._nodes.setdefault(target, "")
            self._triples.add((source, rel_name, target))

    # ---- 读 -------------------------------------------------------------

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._triples)

    def entity_names(self) -> list[str]:
        return sorted(self._nodes)

    def query(self, start_entity_names: list[str], hops: int = 2) -> list[tuple[str, str, str]]:
        """从匹配实体出发 BFS（沿出边与入边同时扩展），返回命中去重三元组，按 (s,r,t) 排序。

        hops 为边扩展层数（1 层 = 直接相连关系）；收集任一端点已访问的边。
        """
        hops = max(1, int(hops))
        visited = {_normalize(n) for n in start_entity_names if _normalize(n)}
        if not visited:
            return []
        out: dict[str, list[tuple[str, str]]] = {}  # node → [(relation, target)]
        into: dict[str, list[tuple[str, str]]] = {}  # node → [(relation, source)]
        for s, r, t in self._triples:
            out.setdefault(s, []).append((r, t))
            into.setdefault(t, []).append((r, s))
        found: set[tuple[str, str, str]] = set()
        frontier = list(visited)
        for _ in range(hops):
            next_frontier: list[str] = []
            for node in frontier:
                for r, t in out.get(node, []):
                    found.add((node, r, t))
                    if t not in visited:
                        visited.add(t)
                        next_frontier.append(t)
                for r, s in into.get(node, []):
                    found.add((s, r, node))
                    if s not in visited:
                        visited.add(s)
                        next_frontier.append(s)
            frontier = next_frontier
            if not frontier:
                break
        return sorted(found)


def match_query_entities(
    store: GraphStore, query: str, max_entities: int = KG_MAX_MATCHED_ENTITIES
) -> list[str]:
    """关键词匹配：查询分词与实体分词存在 _token_matches 即命中。

    零依赖、确定性、离线可测；LLM/嵌入语义实体匹配留作后续增强。
    """
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return []
    matched: list[str] = []
    for name in store.entity_names():
        n_tokens = set(tokenize(name))
        if any(_token_matches(q, n) for q in q_tokens for n in n_tokens):
            matched.append(name)
            if len(matched) >= max_entities:
                break
    return matched
