"""arXiv 论文检索：纯 httpx + xml.etree，零 mcp 依赖、无 key、离线可单测。

供给 MCP「papers」server 作为其 search_papers 工具的底层实现。
- `parse_arxiv_feed` 是纯函数（喂固定 Atom XML 即可测）。
- `search_arxiv` 网络调用不吞异常——由调用方（server 工具）决定如何转可读提示。
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence

import httpx

ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_VERSION_SUFFIX = re.compile(r"v\d+$")


def build_arxiv_params(query: str, max_results: int = 5) -> dict:
    """arXiv API 查询参数：`all:` 全字段检索（标题/作者/摘要）。"""
    return {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": int(max_results),
    }


def parse_arxiv_feed(xml_text: str) -> list[dict]:
    """解析 arXiv Atom feed → [{title, authors, url, summary}]。

    命名空间容错：arXiv 顶层是 Atom feed，entry 内含 author/name 子元素。
    URL 去掉版本后缀（1234.5678v1 → 1234.5678），方便直接打开。
    """
    root = ET.fromstring(xml_text)  # 非法 XML 直接抛错，交由调用方决定
    out: list[dict] = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = _node_text(entry, "title")
        summary = _node_text(entry, "summary")
        url = _node_text(entry, "id")
        if url:
            url = _VERSION_SUFFIX.sub("", url)
        authors = [
            name.text or ""
            for name in entry.findall(f"{_ATOM}author/{_ATOM}name")
        ]
        out.append(
            {"title": title, "authors": authors, "url": url, "summary": summary}
        )
    return out


def _node_text(entry: ET.Element, tag: str) -> str:
    node = entry.find(f"{_ATOM}{tag}")
    return (node.text or "").strip() if node is not None else ""


async def search_arxiv(
    query: str, *, max_results: int = 5, timeout: float = 30.0
) -> list[dict]:
    """调 arXiv API 检索论文（不捕获异常，交给调用方转可读提示）。"""
    params = build_arxiv_params(query, max_results)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        return parse_arxiv_feed(resp.text)


def format_papers(items: Sequence[dict]) -> str:
    """把检索结果格式化成给 LLM/UI 的多行文本（空列表给可读兜底）。"""
    if not items:
        return "（未找到相关论文）"
    lines: list[str] = []
    for i, paper in enumerate(items, start=1):
        authors = paper.get("authors") or []
        author_text = "、".join(authors[:5])
        if len(authors) > 5:
            author_text += " 等"
        summary = (paper.get("summary") or "").replace("\n", " ").strip()
        if len(summary) > 300:
            summary = summary[:300] + "…"
        lines.append(
            f"[{i}] {paper.get('title', '')}\n"
            f"    作者：{author_text or '（佚名）'}\n"
            f"    URL: {paper.get('url', '')}\n"
            f"    {summary}"
        )
    return "\n\n".join(lines)
