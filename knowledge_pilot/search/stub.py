"""Phase 0 占位搜索实现：不联网，返回固定结果，用于跑通链路与离线测试。

接入真实服务商时新增一个类（如 TavilySearchProvider）并在
search/__init__.py 的 _PROVIDERS 中注册即可，无需改动调用方。
"""

from knowledge_pilot.search.base import SearchResult


class StubSearchProvider:
    name = "stub"

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"[Stub] {query} — 结果 {i + 1}",
                url=f"https://stub.example/search?q={query}&n={i}",
                snippet=(
                    f"这是 Phase 0 的 stub 占位结果（未接入真实搜索服务商）。"
                    f"查询词：{query}"
                ),
                content=(
                    "Stub 占位正文：接入 Tavily / DuckDuckGo / Brave 后，"
                    "这里会返回真实网页正文，供后续 RAG 阶段使用。"
                ),
            )
            for i in range(top_k)
        ]
