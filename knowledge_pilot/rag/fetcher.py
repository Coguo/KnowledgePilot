"""网页抓取与正文提取。

- httpx 抓取 HTML（复用现有依赖），trafilatura 提取主正文（去导航/脚本/评论）。
- 失败静默返回 None，不抛异常：抓取是尽力而为，失败项由 ingestion 层回退
  `SearchResult.content`（摘要）。
"""

import asyncio
import html as html_lib
import re
from dataclasses import dataclass

import httpx


@dataclass
class FetchedDocument:
    url: str
    title: str
    text: str
    status_code: int | None = None


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class PageFetcher:
    """抓取单个/多个 URL，返回提取后的正文。"""

    def __init__(self, timeout: float = 15.0, max_bytes: int = 2_000_000) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes

    async def fetch(self, url: str) -> FetchedDocument | None:
        """抓取并提取正文；非 200 / 异常 / 正文为空 → None。"""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=self._timeout
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError:
            return None

        if resp.status_code != 200:
            return None

        import trafilatura  # 懒加载：[rag] extra 依赖，避免 import rag 包即拉入

        html_text = resp.text[: self._max_bytes]
        text = trafilatura.extract(
            html_text, include_comments=False, include_tables=True
        )
        if not text or not text.strip():
            return None
        return FetchedDocument(
            url=url,
            title=_extract_title(html_text) or _fallback_title(url),
            text=text.strip(),
            status_code=resp.status_code,
        )

    async def fetch_many(
        self, urls: list[str], *, concurrency: int = 3
    ) -> list[FetchedDocument]:
        """并发抓取多个 URL，跳过失败项。"""
        sem = asyncio.Semaphore(concurrency)

        async def one(url: str) -> FetchedDocument | None:
            async with sem:
                return await self.fetch(url)

        results = await asyncio.gather(*(one(u) for u in urls))
        return [r for r in results if r is not None]


def _extract_title(html_text: str) -> str:
    m = _TITLE_RE.search(html_text)
    if not m:
        return ""
    return html_lib.unescape(m.group(1)).strip()


def _fallback_title(url: str) -> str:
    """无 <title> 时退回域名。"""
    return url.split("://")[-1].split("/")[0]
