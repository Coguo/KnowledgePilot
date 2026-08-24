"""搜索服务抽象层：服务商可插拔（stub / tavily / 未来的 DuckDuckGo / Brave）。"""

from knowledge_pilot.search.base import SearchProvider, SearchResult
from knowledge_pilot.search.stub import StubSearchProvider
from knowledge_pilot.search.tavily import TavilySearchProvider

# 每个服务商的工厂函数，接收 api_key（不需要的服务商忽略之）。
_PROVIDERS: dict[str, callable] = {
    "stub": lambda api_key: StubSearchProvider(),
    "tavily": lambda api_key: TavilySearchProvider(api_key),
}


def create_search_provider(name: str, api_key: str = "") -> SearchProvider:
    """按配置名创建搜索服务商实例。"""
    try:
        factory = _PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"未知搜索服务商: {name!r}（可用: {', '.join(_PROVIDERS)}）"
        ) from None
    return factory(api_key)


__all__ = ["SearchProvider", "SearchResult", "create_search_provider"]
