"""查询改写：Identity 占位 / LLM 改写 / 空结果与失败回退。"""

import pytest

from knowledge_pilot.rag.rewrite import (
    LLMQueryRewriter,
    IdentityQueryRewriter,
    REWRITE_SYSTEM_PROMPT,
)
from tests.fakes import FakeChatClient


async def test_identity_rewrite_passthrough():
    rewriter = IdentityQueryRewriter()
    assert await rewriter.rewrite("原始问题") == "原始问题"


async def test_llm_rewrite_returns_stripped_text():
    llm = FakeChatClient(script=[])
    llm.complete_output = "  改写后的查询  "
    rewriter = LLMQueryRewriter(llm)

    assert await rewriter.rewrite("原始问题") == "改写后的查询"
    # 消息形状：system 提示 + user 原文
    assert llm.seen_messages[0][0]["role"] == "system"
    assert llm.seen_messages[0][0]["content"] == REWRITE_SYSTEM_PROMPT
    assert llm.seen_messages[0][1] == {"role": "user", "content": "原始问题"}


async def test_llm_rewrite_empty_output_falls_back_to_original():
    llm = FakeChatClient(script=[])
    llm.complete_output = "   "
    rewriter = LLMQueryRewriter(llm)

    assert await rewriter.rewrite("原始问题") == "原始问题"


async def test_llm_rewrite_failure_falls_back_to_original():
    class _FailingLLM:
        model = "fake"

        async def complete(self, messages, *, max_tokens=None):
            raise RuntimeError("网络错误")

    rewriter = LLMQueryRewriter(_FailingLLM())
    assert await rewriter.rewrite("原始问题") == "原始问题"
