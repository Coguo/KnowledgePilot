"""config MCP 开关：默认关向后兼容 + 环境变量覆盖。"""

from knowledge_pilot.config import Settings


def test_mcp_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.mcp_enabled is False  # 默认关：行为与 Phase 5 逐字节一致


def test_mcp_env_overrides(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "true")
    s = Settings(_env_file=None)
    assert s.mcp_enabled is True


def test_mcp_enabled_false_explicit(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.mcp_enabled is False
