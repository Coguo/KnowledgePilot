"""Agent 可用工具：OpenAI function schema 定义 + 执行函数。

新增工具三步：定义 schema → 写执行函数 → 挂到 ALL_TOOLS / run_tool。
"""

from knowledge_pilot.search.base import SearchProvider, SearchResult

SEARCH_WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "当问题需要外部或最新信息（网页、论文、技术文档、代码仓库等）时调用；"
            "返回若干条搜索结果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题，应简洁明确",
                },
            },
            "required": ["query"],
        },
    },
}

ALL_TOOLS = [SEARCH_WEB_TOOL]


async def run_tool(
    name: str,
    arguments: dict,
    *,
    search: SearchProvider,
    rag: "RAGPipeline | None" = None,  # 字符串前向引用，避免 import rag 重依赖
) -> str:
    """执行工具，返回给 LLM 的文本结果（作为 tool message 回填）。

    rag 传入时，search_web 执行后自动抓取结果建库并做向量检索，
    把带来源片段追加进返回文本（RAG 对 LLM 透明）。
    """
    if name == "search_web":
        query = arguments["query"]
        results = await search.search(query)
        base = _format_search_results(results)
        if rag is not None:
            extra = await rag.enrich_search(query, results)
            if extra:
                base = f"{base}\n\n{extra}"
        return base
    raise ValueError(f"未知工具: {name!r}")


def _format_search_results(results: list[SearchResult], limit: int = 3) -> str:
    lines: list[str] = []
    for i, r in enumerate(results[:limit], start=1):
        lines.append(
            f"[{i}] {r.title}\n"
            f"    URL: {r.url}\n"
            f"    {r.snippet}"
        )
        if r.content:
            lines.append(f"    {r.content[:300]}")
    return "\n\n".join(lines)
