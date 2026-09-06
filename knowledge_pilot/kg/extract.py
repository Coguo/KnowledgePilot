"""从研究证据抽取实体与关系（Phase 5）：prompt + 解析 + 文本格式化。

DeepSeek json_object 模式要求 prompt 含单词 "json"（同 planner）。
抽取失败永不阻断研究：返回 ([], [])，由 kg_node 发出 KgEvent(0,0,0)。
"""

from knowledge_pilot.llm.json_utils import parse_json_object

KG_EXTRACT_PROMPT = (
    "你是知识抽取助手。从给定的研究资料中抽取核心实体（概念、技术、方法、工具）"
    "以及它们之间的语义关系，输出为 JSON。\n"
    '严格只输出 JSON（不要任何多余文字），格式为：\n'
    '{"entities": [{"name": "实体名", "type": "concept|method|tool|other"}], '
    '"relations": [{"source": "实体名", "target": "实体名", "relation": "英文动词/短语"}]}\n'
    "要求：\n"
    "1. 实体名统一、避免同义重复；\n"
    "2. 每条关系的 source/target 必须来自 entities；\n"
    "3. 只抽取资料中明确出现的关系，不要臆造。"
)


async def extract_entities_relations(llm, text: str):
    """抽取实体与关系；任何失败返回 ([], [])，永不抛异常、永不阻断研究。"""
    text = (text or "").strip()
    if not text:
        return [], []
    prompt = [
        {"role": "system", "content": KG_EXTRACT_PROMPT},
        {"role": "user", "content": f"请从以下资料抽取实体与关系：\n{text}"},
    ]
    try:
        raw = await llm.complete(prompt, response_format={"type": "json_object"})
    except Exception:
        return [], []
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return [], []
    return _valid_entities(parsed.get("entities")), _valid_relations(parsed.get("relations"))


def _valid_entities(raw) -> list[dict]:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append({"name": name, "type": str(item.get("type") or "")})
    return out


def _valid_relations(raw) -> list[dict]:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        relation = str(item.get("relation") or "").strip()
        if not source or not target or not relation:
            continue
        out.append({"source": source, "target": target, "relation": relation})
    return out


def format_triples(triples) -> str:
    """[(source, relation, target)] → 每行 "- source -[relation]-> target"。"""
    return "\n".join(f"- {s} -[{r}]-> {t}" for s, r, t in triples)


def build_kg_context(triples) -> str:
    """把三元组拼成注入 synthesize 的「相关实体关系」块；空 → ""。"""
    if not triples:
        return ""
    return "相关实体关系（知识图谱）：\n" + format_triples(triples)
