"""搜索服务抽象层：服务商可插拔（stub / 未来的 Tavily / DuckDuckGo / Brave）。"""

from knowledge_pilot.search.base import SearchProvider, SearchResult
from knowledge_pilot.search.stub import StubSearchProvider

_PROVIDERS: dict[str, type[SearchProvider]] = {
    "stub": StubSearchProvider,
}


def create_search_provider(name: str) -> SearchProvider:
    """按配置名创建搜索服务商实例。"""
    try:
        cls = _PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"未知搜索服务商: {name!r}（可用: {', '.join(_PROVIDERS)}）"
        ) from None
    return cls()


__all__ = ["SearchProvider", "SearchResult", "create_search_provider"]
