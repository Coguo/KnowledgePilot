"""查询改写：把研究问题改写成更适合检索的关键词查询。

- `QueryRewriter` Protocol：运行时可注入 LLM 改写；评测离线模式用 Identity。
- `LLMQueryRewriter`：复用现有 ChatClient（DeepSeek），每次改写一次非流式调用。
  默认运行时关闭（省一次 LLM 调用的延迟与成本），评测矩阵里测量其收益，
  用数据回答"改写是否值得"。
"""

from typing import Protocol

from knowledge_pilot.llm.client import LLMClient


REWRITE_SYSTEM_PROMPT = (
    "你是检索查询改写器。把用户的研究问题改写成更适合检索的简洁关键词查询："
    "保留核心术语，去掉冗余修饰。只输出改写后的查询，不要任何解释。"
)


class QueryRewriter(Protocol):
    """查询改写器契约。"""

    async def rewrite(self, query: str) -> str: ...


class IdentityQueryRewriter:
    """原样返回：评测离线模式 / 未启用改写的占位实现。"""

    async def rewrite(self, query: str) -> str:
        return query


class LLMQueryRewriter:
    """用 LLM 改写查询；空结果 / 调用失败时回退原查询，不阻塞检索。"""

    def __init__(self, llm: LLMClient, *, max_tokens: int = 64) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    async def rewrite(self, query: str) -> str:
        messages = [
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        try:
            rewritten = await self._llm.complete(
                messages, max_tokens=self._max_tokens
            )
        except Exception:
            return query  # 调用失败不阻塞检索
        rewritten = rewritten.strip()
        return rewritten if rewritten else query
