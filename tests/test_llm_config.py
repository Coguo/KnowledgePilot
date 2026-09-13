"""Model Gateway 配置（Phase 8）：默认全关/空（= 旧 ChatClient 逐字节一致）+ 环境覆盖
+ `build_llm_client` 的装配决策（单 provider / max_retries=0 防叠乘 / fallback 注册）。

构造 ProviderClient 不会 import openai（客户端是懒建的），故本文件可在离线沙箱运行。
"""

import pytest

from knowledge_pilot.config import LLMProviderSettings, Settings
from knowledge_pilot.llm.gateway import build_llm_client

_EXTRA_JSON = (
    '[{"name":"qwen","model":"qwen-plus",'
    '"base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1",'
    '"api_key":"sk-q","timeout":30}]'
)


def _settings(monkeypatch, **env) -> Settings:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


# ---- 默认值（全关/空 = 兼容路径）----------------------------------------

def test_gateway_defaults_without_env():
    s = Settings(_env_file=None)
    assert s.llm_timeout is None  # 不设 → 不传 → SDK 默认超时
    assert s.llm_retry_enabled is False
    assert s.llm_retry_max_attempts == 3
    assert s.llm_retry_backoff_seconds == 1.0
    assert s.llm_fallback_enabled is False
    assert s.llm_log_usage is False
    assert s.llm_extra_providers == []


def test_gateway_env_overrides(monkeypatch):
    s = _settings(
        monkeypatch,
        LLM_TIMEOUT="60",
        LLM_RETRY_ENABLED="true",
        LLM_RETRY_MAX_ATTEMPTS="5",
        LLM_RETRY_BACKOFF_SECONDS="0.5",
        LLM_FALLBACK_ENABLED="true",
        LLM_LOG_USAGE="true",
    )
    assert s.llm_timeout == 60.0
    assert s.llm_retry_enabled is True
    assert s.llm_retry_max_attempts == 5
    assert s.llm_retry_backoff_seconds == 0.5
    assert s.llm_fallback_enabled is True
    assert s.llm_log_usage is True


def test_extra_providers_from_json_env(monkeypatch):
    s = _settings(monkeypatch, LLM_EXTRA_PROVIDERS=_EXTRA_JSON)
    assert len(s.llm_extra_providers) == 1
    extra = s.llm_extra_providers[0]
    assert (extra.name, extra.model, extra.api_key, extra.timeout) == (
        "qwen", "qwen-plus", "sk-q", 30.0,
    )


def test_extra_providers_direct_construction():
    s = Settings(
        _env_file=None,
        llm_extra_providers=[LLMProviderSettings(
            name="qwen", model="qwen-plus", base_url="https://x/v1", api_key="sk-q",
        )],
    )
    assert s.llm_extra_providers[0].name == "qwen"
    assert s.llm_extra_providers[0].timeout is None


# ---- build_llm_client 装配 ----------------------------------------------

def test_build_default_is_single_deepseek_and_sdk_retries_preserved(monkeypatch):
    gw = build_llm_client(_settings(monkeypatch))
    assert gw.model == "deepseek-chat"  # 对外 model = primary
    assert len(gw._providers) == 1
    provider = gw._providers[0]
    assert provider.name == "deepseek"
    # 网关不自管重试 → 不传 max_retries → 保留 SDK 默认（与旧 ChatClient 一致）。
    assert provider._max_retries is None
    assert provider._timeout is None


def test_build_passes_timeout(monkeypatch):
    gw = build_llm_client(_settings(monkeypatch, LLM_TIMEOUT="45"))
    assert gw._providers[0]._timeout == 45.0


def test_build_retry_enabled_disables_sdk_retries(monkeypatch):
    gw = build_llm_client(_settings(monkeypatch, LLM_RETRY_ENABLED="true"))
    # 网关独占重试决策 → SDK 内置重试必须为 0，防叠乘。
    assert gw._providers[0]._max_retries == 0


def test_build_registers_fallback_provider(monkeypatch):
    gw = build_llm_client(_settings(
        monkeypatch, LLM_FALLBACK_ENABLED="true", LLM_EXTRA_PROVIDERS=_EXTRA_JSON,
    ))
    assert [p.name for p in gw._providers] == ["deepseek", "qwen"]
    assert all(p._max_retries == 0 for p in gw._providers)  # fallback 也关 SDK 重试


def test_build_skips_extra_provider_without_key(monkeypatch):
    empty_key = _EXTRA_JSON.replace('"sk-q"', '""')
    gw = build_llm_client(_settings(
        monkeypatch, LLM_FALLBACK_ENABLED="true", LLM_EXTRA_PROVIDERS=empty_key,
    ))
    assert [p.name for p in gw._providers] == ["deepseek"]  # 半配置的 provider 不纳入


def test_build_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError):
        build_llm_client(Settings(_env_file=None))
