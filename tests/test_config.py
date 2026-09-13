"""config 模块：默认值 / 环境变量覆盖 / 未配置 key 的行为。"""

from knowledge_pilot.config import Settings


def test_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.deepseek_api_key == ""
    assert s.deepseek_base_url == "https://api.deepseek.com"
    assert s.deepseek_model == "deepseek-chat"
    assert s.app_host == "127.0.0.1"
    assert s.app_port == 8000
    assert s.search_provider == "stub"
    assert s.has_api_key is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("SEARCH_PROVIDER", "stub")

    s = Settings(_env_file=None)
    assert s.deepseek_api_key == "sk-test"
    assert s.deepseek_model == "deepseek-reasoner"
    assert s.app_port == 9000
    assert s.has_api_key is True


def test_rag_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.rag_enabled is False
    assert s.embedding_model == "BAAI/bge-m3"
    assert s.chroma_dir == "./data/chroma"
    assert s.rag_chunk_size == 800
    assert s.rag_chunk_overlap == 200
    assert s.rag_top_k == 3
    assert s.rag_max_fetch_urls == 3


def test_agent_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.agent_mode == "graph"
    assert s.agent_max_iterations == 3


def test_memory_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.memory_enabled is False
    assert s.memory_db_path == "./data/research_memory.db"
    assert s.memory_checkpoint_db_path == "./data/graph_checkpoints.db"
    assert s.memory_top_k == 3


def test_memory_env_overrides(monkeypatch):
    monkeypatch.setenv("MEMORY_ENABLED", "true")
    monkeypatch.setenv("MEMORY_TOP_K", "5")
    s = Settings(_env_file=None)
    assert s.memory_enabled is True
    assert s.memory_top_k == 5


def test_kg_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.kg_enabled is False
    assert s.kg_hops == 2


def test_kg_env_overrides(monkeypatch):
    monkeypatch.setenv("KG_ENABLED", "true")
    monkeypatch.setenv("KG_HOPS", "3")
    s = Settings(_env_file=None)
    assert s.kg_enabled is True
    assert s.kg_hops == 3


def test_rag_env_overrides(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_TOP_K", "5")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    s = Settings(_env_file=None)
    assert s.rag_enabled is True
    assert s.rag_top_k == 5
    assert s.embedding_model == "BAAI/bge-small-zh-v1.5"


# ---- Phase 8：Model Gateway 的空值容错 ----
# 「留空最自然」的字段是启动期地雷：LLM_TIMEOUT= 的空串不是合法 float、
# LLM_EXTRA_PROVIDERS= 的空串不是合法 JSON（后者还抛在数据源层，字段校验器轮不到），
# 两者都在 config **模块导入期**炸，应用连启动都到不了。这类崩法当时全量测试一条都没盖到。


def test_llm_gateway_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.llm_timeout is None
    assert s.llm_retry_enabled is False
    assert s.llm_fallback_enabled is False
    assert s.llm_log_usage is False
    assert s.llm_extra_providers == []


def test_blank_llm_fields_are_read_as_unset(monkeypatch):
    """`.env` 里写 `KEY=`（留空）→ 当作未设置，而不是启动失败。"""
    monkeypatch.setenv("LLM_TIMEOUT", "")
    monkeypatch.setenv("LLM_EXTRA_PROVIDERS", "")
    s = Settings(_env_file=None)
    assert s.llm_timeout is None
    assert s.llm_extra_providers == []


def test_whitespace_only_llm_fields_are_read_as_unset(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "   ")
    monkeypatch.setenv("LLM_EXTRA_PROVIDERS", "   ")
    s = Settings(_env_file=None)
    assert s.llm_timeout is None
    assert s.llm_extra_providers == []


def test_llm_timeout_and_extra_providers_values_still_parse(monkeypatch):
    """空值容错不能把「真有值」的路也吃掉（NoDecode 关掉了自动 JSON 解码）。"""
    monkeypatch.setenv("LLM_TIMEOUT", "30")
    monkeypatch.setenv(
        "LLM_EXTRA_PROVIDERS",
        '[{"name":"qwen","model":"qwen-plus","base_url":"https://example.invalid/v1",'
        '"api_key":"sk-placeholder","timeout":60}]',
    )
    s = Settings(_env_file=None)
    assert s.llm_timeout == 30.0
    assert len(s.llm_extra_providers) == 1
    assert s.llm_extra_providers[0].name == "qwen"
    assert s.llm_extra_providers[0].timeout == 60.0


def test_shipped_env_example_is_loadable(monkeypatch):
    """`.env.example` 是给用户 copy 的模板——它必须真的能加载。

    这是上面那类 bug 最直接的守护：模板里的空值项一旦不可解析，**每一个照抄模板启动的
    人都会在 config 导入期崩掉**，症状是「我不知道怎么启动这个项目」。
    """
    from pathlib import Path

    # 清掉环境变量，免得本机 shell 里的同名变量把模板值盖掉，测出假绿。
    for key in ("LLM_TIMEOUT", "LLM_EXTRA_PROVIDERS"):
        monkeypatch.delenv(key, raising=False)

    example = Path(__file__).resolve().parents[1] / ".env.example"
    s = Settings(_env_file=example)  # 能构造出来即达标

    assert s.llm_timeout is None  # 模板里是空的
    assert s.llm_extra_providers == []
    assert s.agent_mode in ("graph", "loop")
