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


def test_rag_env_overrides(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_TOP_K", "5")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    s = Settings(_env_file=None)
    assert s.rag_enabled is True
    assert s.rag_top_k == 5
    assert s.embedding_model == "BAAI/bge-small-zh-v1.5"
