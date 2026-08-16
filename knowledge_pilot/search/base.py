"""搜索接口定义：UI / 服务商无关，所有 Provider 必须实现该契约。"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SearchResult:
    """一条搜索结果。"""

    title: str
    url: str
    snippet: str
    content: str | None = None  # 干净的网页正文（供后续 RAG 阶段使用）
    metadata: dict[str, str] = field(default_factory=dict)


class SearchProvider(Protocol):
    """搜索服务商契约。"""

    name: str

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """按查询词返回最多 top_k 条结果。"""
        ...
