"""真实组件：`--real` 模式用真实 DeepSeek LLM / 真实搜索 / LLM judge。

口径（写进 docs）：
- CountingChatClient 包一层 ChatClient，**只加记账不改行为**（stream_calls /
  complete_calls / tokens 与离线 ScriptedChatClient 同字段同口径——token 复用
  offline.estimate_messages_tokens，两种模式可比）。
- search 走 create_search_provider（SEARCH_PROVIDER=tavily 全真实；=stub 仍可
  纯 LLM+judge，验证质量轴不联网）。
- memory 用 tempfile 全新 db（**绝不触碰 settings.memory_db_path 用户库**），
  每条 (item, variant) 独立 + pre_seed 预置，隔离语义与离线一致。
- judge 用**独立** ChatClient：judge 的 token/调用不污染 agent 的计数。
- mcp = None：MCP servers 需独立进程，--real 不接线（`all` 在 --real 下实际等于
  graph+memory+kg，模型无 MCP 工具可调——docs 注明该轴只在离线可完整演示）。
- warmup 复用离线脚本化组件（不烧 key/不联网）：预热只为吸收 graph 模块懒导入/
  首次编译的开销，真实 LLM 调用本身不计入延迟列即可，无需为预热付真 token。

仅当 CLI 显式传 `--real`（且 .env 配了 DEEPSEEK_API_KEY）才被 import。
"""

import os
import tempfile

from knowledge_pilot.agent.eval.dataset import AgentItem
from knowledge_pilot.agent.eval.offline import (
    PreparedRun,
    Variant,
    estimate_messages_tokens,
    make_offline_components,
)
from knowledge_pilot.config import Settings
from knowledge_pilot.llm.client import ChatClient
from knowledge_pilot.memory.store import ResearchMemoryStore


class CountingChatClient:
    """只做调用的计数包装（stream/complete 次数 + context token 启发式）。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.stream_calls = 0
        self.complete_calls = 0
        self.tokens = 0

    @property
    def model(self) -> str:
        return self._inner.model

    async def stream_chat(self, messages, tools=None):
        self.stream_calls += 1
        self.tokens += estimate_messages_tokens(messages)
        async for chunk in self._inner.stream_chat(messages, tools=tools):
            yield chunk

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        self.complete_calls += 1
        self.tokens += estimate_messages_tokens(messages)
        return await self._inner.complete(
            messages, max_tokens=max_tokens, response_format=response_format
        )


def _seed_memory(item: AgentItem) -> tuple[ResearchMemoryStore, tempfile.TemporaryDirectory]:
    """为一条 (item, variant) 建全新记忆库并按 pre_seed 预置（隔离，绝不动用户库）。"""
    tmpdir = tempfile.TemporaryDirectory(prefix="kp_eval_real_mem_")
    store = ResearchMemoryStore(os.path.join(tmpdir.name, "memory.db"))
    for seed in item.pre_seed:
        store.save_run(seed.query, report=seed.report, sources=list(seed.sources))
    return store, tmpdir


class _RealComponents:
    """真实组件工厂（runner 消费；与离线 _OfflineComponents 同形状）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        if not self._settings.has_api_key:
            raise ValueError(
                "--real 需 .env 配置 DEEPSEEK_API_KEY（复制 .env.example 为 .env 并填入）。"
            )

    # ---- judge 用独立 ChatClient：token/调用不计入 agent 计数 -------------
    def make_judge(self):
        from knowledge_pilot.agent.eval.judge import DeepSeekJudge

        return DeepSeekJudge(ChatClient(self._settings))

    def make_context(
        self, item: AgentItem, variant: Variant, *, max_iterations: int
    ) -> PreparedRun:
        from knowledge_pilot.search import create_search_provider

        llm = CountingChatClient(ChatClient(self._settings))
        search = create_search_provider(
            self._settings.search_provider, self._settings.tavily_api_key
        )
        memory = None
        tmpdir = None
        if variant.use_memory:
            memory, tmpdir = _seed_memory(item)
        # mcp 需独立进程 server；--real 不接线（见模块 docstring）。
        return PreparedRun(
            llm=llm,
            search=search,
            memory=memory,
            mcp=None,
            _tmpdir=tmpdir,
        )

    def make_warmup_context(self, variant: Variant) -> PreparedRun | None:
        """预热 = 离线脚本化（只吸收懒导入/首次编译，不烧真 token 也不进延迟列）。"""
        return make_offline_components().make_warmup_context(variant)


def make_real_components(settings: Settings | None = None) -> _RealComponents:
    """用配置装配真实组件；无 DEEPSEEK_API_KEY 时抛 ValueError（CLI 引导用户）。"""
    return _RealComponents(settings)
