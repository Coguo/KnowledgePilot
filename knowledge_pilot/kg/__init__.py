"""Knowledge Graph（Phase 5）：per-task 内存知识图谱，零依赖。

核心版：evidence → LLM 实体关系抽取 → GraphStore → 查询关键词匹配 → BFS 展开
→ 注入 synthesize 报告 prompt。不做跨任务积累/持久化/query_knowledge_graph 工具。
本阶段无需工厂：图是每次任务的临时对象，API 层只透传两个配置开关。
"""

from knowledge_pilot.kg.extract import (
    build_kg_context,
    extract_entities_relations,
    format_triples,
)
from knowledge_pilot.kg.graph import GraphStore, match_query_entities, tokenize

__all__ = [
    "GraphStore",
    "tokenize",
    "match_query_entities",
    "extract_entities_relations",
    "format_triples",
    "build_kg_context",
]
