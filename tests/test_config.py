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
