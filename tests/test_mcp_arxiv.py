"""arXiv 检索模块（纯 httpx + xml.etree，零 mcp 依赖）单测。

feed 解析喂固定 XML 样本；search_arxiv 用假 httpx.AsyncClient 拦截网络层，
断言 URL / 参数 / 异常上抛，全程不联网。
"""

import httpx
import pytest

from knowledge_pilot.mcp import arxiv

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query: RAG chunking</title>
  <entry>
    <id>http://arxiv.org/abs/2310.12345v2</id>
    <title>Attention Is All You Need</title>
    <summary>We propose a new architecture based on attention.</summary>
    <author><name>Vaswani</name></author>
    <author><name>Shazeer</name></author>
  </entry>
</feed>
"""

EMPTY_FEED = '<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


# ---- build_arxiv_params ------------------------------------------------


def test_build_params_prefixes_all_and_passthrough():
    assert arxiv.build_arxiv_params("RAG chunking", 5) == {
        "search_query": "all:RAG chunking",
        "start": 0,
        "max_results": 5,
    }


# ---- parse_arxiv_feed ---------------------------------------------------


def test_parse_single_entry():
    items = arxiv.parse_arxiv_feed(SAMPLE_FEED)
    assert len(items) == 1
    paper = items[0]
    assert paper["title"] == "Attention Is All You Need"
    assert paper["authors"] == ["Vaswani", "Shazeer"]
    assert paper["url"] == "http://arxiv.org/abs/2310.12345"  # v2 版本后缀被剥
    assert paper["summary"] == "We propose a new architecture based on attention."


def test_parse_empty_feed():
    assert arxiv.parse_arxiv_feed(EMPTY_FEED) == []


def test_parse_entry_without_authors():
    xml = SAMPLE_FEED.replace("<author><name>Vaswani</name></author>", "").replace(
        "<author><name>Shazeer</name></author>", ""
    )
    items = arxiv.parse_arxiv_feed(xml)
    assert items[0]["authors"] == []


def test_parse_strips_whitespace_title():
    xml = SAMPLE_FEED.replace("Attention Is All You Need", "  Spaced Title  ")
    items = arxiv.parse_arxiv_feed(xml)
    assert items[0]["title"] == "Spaced Title"


def test_parse_invalid_xml_raises():
    with pytest.raises(Exception):
        arxiv.parse_arxiv_feed("<not-xml")


# ---- search_arxiv（假 httpx） -------------------------------------------


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, xml, error=None):
        self.xml = xml
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.xml)


async def test_search_arxiv_hits_api_and_parses(monkeypatch):
    fake = _FakeClient(SAMPLE_FEED)
    monkeypatch.setattr(arxiv.httpx, "AsyncClient", lambda **kw: fake)

    items = await arxiv.search_arxiv("RAG chunking")
    assert len(items) == 1
    assert items[0]["url"] == "http://arxiv.org/abs/2310.12345"

    assert len(fake.calls) == 1
    url, params = fake.calls[0]
    assert url == arxiv.ARXIV_API
    assert params["search_query"] == "all:RAG chunking"
    assert params["max_results"] == 5


async def test_search_arxiv_propagates_http_error(monkeypatch):
    err = httpx.HTTPStatusError("500", request=None, response=None)
    fake = _FakeClient(SAMPLE_FEED, error=err)
    monkeypatch.setattr(arxiv.httpx, "AsyncClient", lambda **kw: fake)

    with pytest.raises(httpx.HTTPStatusError):
        await arxiv.search_arxiv("broken")


async def test_search_arxiv_constructs_client_with_timeout(monkeypatch):
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            return _FakeResponse(SAMPLE_FEED)

    monkeypatch.setattr(arxiv.httpx, "AsyncClient", _FakeClient)
    await arxiv.search_arxiv("q", timeout=7.0)
    assert seen.get("timeout") == 7.0


# ---- format_papers ------------------------------------------------------


def test_format_papers_single():
    items = [{"title": "T", "authors": ["A", "B"], "url": "http://x/1", "summary": "S"}]
    out = arxiv.format_papers(items)
    assert out.startswith("[1] T")
    assert "作者：A、B" in out
    assert "URL: http://x/1" in out
    assert "S" in out


def test_format_papers_many_authors_truncated():
    authors = [f"a{i}" for i in range(8)]
    out = arxiv.format_papers([{"title": "T", "authors": authors, "url": "u", "summary": "s"}])
    assert "a0、a1、a2、a3、a4 等" in out
    assert "a7" not in out


def test_format_papers_long_summary_truncated():
    long_summary = "x" * 500
    out = arxiv.format_papers([{"title": "T", "authors": [], "url": "u", "summary": long_summary}])
    assert "x" * 300 + "…" in out
    assert "作者：（佚名）" in out


def test_format_papers_collapses_summary_newlines():
    items = [{"title": "T", "authors": [], "url": "u", "summary": "line1\nline2"}]
    out = arxiv.format_papers(items)
    assert "line1 line2" in out


def test_format_papers_empty():
    assert arxiv.format_papers([]) == "（未找到相关论文）"
