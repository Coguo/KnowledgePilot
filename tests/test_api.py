"""API 层：SSE 端点冒烟测试（依赖注入覆盖，不联网）。

依赖 get_chat_deps 被覆盖为 Fake LLM + Stub 搜索，因此请求不会触达任何真实服务。
"""

import json

from httpx import ASGITransport, AsyncClient

from knowledge_pilot.api import main as api_main
from knowledge_pilot.api.main import ChatDeps, app, get_chat_deps
from knowledge_pilot.search.stub import StubSearchProvider

from tests.fakes import FakeChatClient


def _override_deps(script=None):
    llm = FakeChatClient(script=script or [(["接口测试回答。"], [])])
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider()
    )
    return llm


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_index_served():
    async with await _client() as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "Research Chat" in resp.text


async def test_chat_streams_events():
    _override_deps()
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "你好"}
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")

                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()

    assert frames[-1] == "[DONE]"
    types = [f["type"] for f in frames[:-1]]
    assert types == ["token", "done"]
    content = "".join(
        f["content"] for f in frames[:-1] if f.get("type") == "token"
    )
    assert "接口测试回答" in content


async def test_chat_streams_tool_events():
    llm = _override_deps(script=[
        ([], [{"name": "search_web", "arguments": '{"query": "测试"}'}]),
        (["基于搜索的答案。"], []),
    ])
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "搜索一下"}
            ) as resp:
                assert resp.status_code == 200
                frames = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        frames.append("[DONE]" if data == "[DONE]" else json.loads(data))
    finally:
        app.dependency_overrides.clear()

    types = [f["type"] for f in frames if f != "[DONE]"]
    assert types == ["tool_call", "tool_result", "token", "done"]
    assert llm.calls == 2


async def test_chat_closes_rag_after_stream():
    """流结束后 RAGPipeline.close() 被调用（清理 task_{uuid} 临时知识库）。"""
    closed = []

    class _FakeRag:
        def close(self):
            closed.append(True)

    llm = FakeChatClient(script=[(["接口测试回答。"], [])])
    app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
        llm=llm, search=StubSearchProvider(), rag=_FakeRag()
    )
    try:
        async with await _client() as client:
            async with client.stream(
                "POST", "/api/chat", json={"message": "你好"}
            ) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    pass
    finally:
        app.dependency_overrides.clear()

    assert closed == [True]


async def test_chat_requires_api_key(monkeypatch):
    """未配置密钥时返回清晰错误，而不是神秘的 500。"""
    monkeypatch.setattr(api_main.settings, "deepseek_api_key", "")
    async with await _client() as client:
        resp = await client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 500
    assert "DEEPSEEK_API_KEY" in resp.json()["detail"]
