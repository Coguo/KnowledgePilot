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


async def run_tool(name: str, arguments: dict, *, search: SearchProvider) -> str:
    """执行工具，返回给 LLM 的文本结果（作为 tool message 回填）。"""
    if name == "search_web":
        results = await search.search(arguments["query"])
        return _format_search_results(results)
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
