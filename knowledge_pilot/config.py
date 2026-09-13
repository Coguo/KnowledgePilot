"""应用配置：全部来自环境变量 / .env 文件，禁止在代码中硬编码密钥。

用法：
    from knowledge_pilot.config import settings
    settings.deepseek_api_key  # 空字符串表示未配置
"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LLMProviderSettings(BaseModel):
    """Model Gateway 的 fallback provider（OpenAI 兼容接口，如 Qwen）。

    经 JSON 环境变量注入（.env 里 JSON 数组一行），默认空 → 单 DeepSeek provider。
    api_key 允许空字符串（build_llm_client 里跳过空 key 的 provider，避免半配置报错）。
    """

    name: str
    model: str
    base_url: str
    api_key: str = ""
    timeout: float | None = None  # None → 不传 → SDK 默认


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek（OpenAI 兼容接口）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # 本地服务
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # 搜索服务商（stub 占位 / tavily 真实搜索）
    search_provider: str = "stub"
    tavily_api_key: str = ""

    # RAG（Phase 1，可选；重依赖懒加载，未启用时应用照常启动）
    rag_enabled: bool = False  # 默认关：避免首次使用无预警下载约 2GB 模型
    embedding_model: str = "BAAI/bge-m3"
    embedding_cache_dir: str = ""  # 空 → HF 默认缓存；建议 data/models
    embedding_device: str = "cpu"
    chroma_dir: str = "./data/chroma"
    rag_chunk_strategy: str = "fixed"  # fixed / recursive
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 200
    rag_top_k: int = 3
    rag_max_fetch_urls: int = 3
    rag_fetch_timeout: float = 15.0

    # Agent 编排（Phase 3）
    # agent_mode: graph（LangGraph：拆解→研究→评估→综合报告） / loop（Phase 0-2 单轮工具循环）
    agent_mode: Literal["graph", "loop"] = "graph"
    agent_max_iterations: int = 3  # 研究-评估条件循环上限（防死循环）
    # Phase 9：把原本硬编码在 loop.py 的 MAX_TOOL_ROUNDS 提上来（值不变），并给请求级
    # 覆盖一个硬上限——两处轮数上限会**相乘**（最坏 3 × 4 = 12 次工具调用 LLM 调用），
    # 所以只允许请求端在同一量级内微调，不允许它把上限开到任意大。
    agent_max_tool_rounds: int = 4  # 单次研究的工具调用轮次上限
    agent_rounds_hard_cap: int = 10  # 请求级 max_iterations/max_tool_rounds 的钳制上界

    # Memory（Phase 4，可选；纯 stdlib sqlite3，默认关向后兼容）
    memory_enabled: bool = False  # 开启后：研究历史落库 + 同类问题复用 + checkpoint 持久化
    memory_db_path: str = "./data/research_memory.db"  # 研究历史（query/plan/report/来源）
    memory_checkpoint_db_path: str = "./data/graph_checkpoints.db"  # 图 checkpoint（SqliteSaver）
    memory_top_k: int = 3  # 新研究开始时召回的历史研究条数

    # Knowledge Graph（Phase 5，可选；纯 stdlib 内存图，默认关向后兼容）
    kg_enabled: bool = False  # 开启后：研究结束前从证据抽实体关系建图，注入报告 prompt
    kg_hops: int = 2  # 从匹配实体 BFS 扩展的边层数（1 层 = 直接相连关系）

    # MCP（Phase 6，可选；stdio 子进程 server + 官方 mcp SDK client）
    # 开启后 research 循环多出 search_memory/recent_research/search_papers（只读）。
    # 默认关向后兼容（与 Phase 5 行为逐字节一致）；只在 AGENT_MODE=graph 生效。
    mcp_enabled: bool = False

    # 学习图谱（Phase 9，可选；纯 stdlib sqlite3 存结构与状态 + Markdown 存正文）
    # 开启后 /api/learning/* 可用（主题 → 研究 → 学习路径图谱 → 逐点讲解 → 点亮）。
    # 默认关向后兼容：关闭时这些路由统一返 503，其余功能与 Phase 8 逐字节一致。
    learning_enabled: bool = True  # 总开关（默认开：主页就是这个界面）；GET 端点不需要 LLM key（浏览已有图谱可离线）
    learning_db_path: str = "./data/learning.db"  # 主题/知识点/前置关系/学习状态/对话
    learning_notes_dir: str = "./data/knowledge"  # 每个知识点的讲解正文（Markdown）
    learning_max_nodes: int = 12  # 单主题知识点上限（超出截断，防大图糊成一团）
    learning_recommend_enabled: bool = True  # 讲解后让 LLM 判定「可以点亮了吗」
    learning_recommend_min_turns: int = 1  # 至少聊够几轮才判定（省掉没必要的调用）
    learning_mastery_confidence: float = 0.7  # 推荐点亮的置信度阈值（低于此值不推荐）
    learning_auto_explain: bool = False  # 打开节点是否自动开讲；默认否 = 纯读已存正文
    learning_outline_max_items: int = 6  # 按需生成的提纲最多几条（见 learning/outline.py）

    # Model Gateway（Phase 8，可选；统一模型访问层——超时/重试/fallback/usage 日志）
    # 默认全关/空 = 单 DeepSeek provider、一次调用、SDK 默认超时/重试 → 与旧 ChatClient
    # 逐字节一致。全部 opt-in，只在想要传输加固时开启。
    llm_timeout: float | None = None  # None → 不传 → SDK 默认超时
    llm_retry_enabled: bool = False  # 开启 → 网关对瞬时错误（连接/超时/429/5xx）退避重试
    llm_retry_max_attempts: int = 3  # 单 provider 最多尝试次数（>=1）
    llm_retry_backoff_seconds: float = 1.0  # 退避基数（attempt 线性递增）
    llm_fallback_enabled: bool = False  # 开启 → primary 瞬时错耗尽后顺延 llm_extra_providers
    llm_log_usage: bool = False  # 开启 → 记录每次调用的 token usage 到 stderr 日志
    # NoDecode = 自己接管本字段的 JSON 解码（见下方 validator）。复杂字段留空时，人
    # 第一反应就是写 `LLM_EXTRA_PROVIDERS=`，而那并不是合法 JSON。
    llm_extra_providers: Annotated[list[LLMProviderSettings], NoDecode] = []  # fallback 目标；默认空

    @field_validator("llm_extra_providers", mode="before")
    @classmethod
    def _decode_extra_providers(cls, value: object) -> object:
        """空值 → []，非空字符串 → 自行 json 解码，其余原样透传。

        pydantic-settings 默认在**数据源层**就给复杂字段做 json 解码，空字符串不是合法
        JSON → 直接抛 SettingsError，且抛在模块导入期，应用连启动都到不了，字段校验器
        根本轮不到。故用 NoDecode 关掉自动解码、在这里接管：`LLM_EXTRA_PROVIDERS=`
        （.env / .env.example 里最自然的写法）读作「没有备用 provider」，
        `LLM_EXTRA_PROVIDERS=[{...}]` 仍按 JSON 解析。
        """
        if isinstance(value, str):
            if not value.strip():
                return []
            return json.loads(value)
        return value

    @field_validator("llm_timeout", mode="before")
    @classmethod
    def _blank_timeout_means_sdk_default(cls, value: object) -> object:
        """`LLM_TIMEOUT=`（留空）→ None = 用 SDK 默认超时，而不是启动失败。

        与上一个校验器同源：`.env` 里留空是最自然的写法，但空字符串不是合法 float。
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # RAG Optimization（Phase 2，可选旋钮；全部可插拔，默认开启 Hybrid + Rerank）
    rag_hybrid_enabled: bool = True  # BM25 + 向量 RRF 混合搜索
    rag_rerank_enabled: bool = True  # bge-reranker-base CrossEncoder 精排
    rag_rerank_model: str = "BAAI/bge-reranker-base"
    rag_rerank_candidates: int = 20  # 精排候选池大小（也作混合检索每路候选数）
    rag_query_rewrite_enabled: bool = False  # 默认关：每次 search_web 多一次 LLM 调用

    @property
    def has_api_key(self) -> bool:
        """是否已配置 LLM 密钥。"""
        return bool(self.deepseek_api_key.strip())


def clamp_rounds(value: int | None, default: int, cap: int) -> int:
    """请求级轮数覆盖 → 钳到 [1, cap]；value 为 None 时直接用 default（不钳）。

    只钳**请求值**：`.env` 里配的 agent_max_iterations / agent_max_tool_rounds 是运维
    自己的选择，不该被这个请求边界函数改写（否则把 .env 调到 20 会被悄悄压成 10）。
    """
    if value is None:
        return default
    return max(1, min(int(value), cap))


# 模块级单例：应用启动时从环境变量 / .env 读取。
settings = Settings()
