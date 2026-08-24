"""Tavily 搜索实现：通过 Tavily REST API 返回真实搜索结果。

- 使用 httpx 直接调 API（透明、少一个 SDK 依赖；httpx 后续抓网页正文也要用）。
- 免费额度：https://tavily.com 注册获取 API key。
"""

import httpx

from knowledge_pilot.search.base import SearchResult

TAVILY_ENDPOINT = "https://api.tavily.com/search"
TAVILY_TIMEOUT = 20.0


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str) -> None:
        if not api_key.strip():
            raise ValueError(
                "未配置 TAVILY_API_KEY：请在 .env 中填入后重试。"
                "注册地址：https://tavily.com"
            )
        self._api_key = api_key.strip()

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": top_k,
        }
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT) as client:
            resp = await client.post(TAVILY_ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            content = item.get("content", "")
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=content[:200],
                    content=content,
                    metadata={"score": str(item.get("score", ""))},
                )
            )
        return results
