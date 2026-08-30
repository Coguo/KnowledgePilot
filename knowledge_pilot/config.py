"""应用配置：全部来自环境变量 / .env 文件，禁止在代码中硬编码密钥。

用法：
    from knowledge_pilot.config import settings
    settings.deepseek_api_key  # 空字符串表示未配置
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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


# 模块级单例：应用启动时从环境变量 / .env 读取。
settings = Settings()
