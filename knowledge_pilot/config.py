"""应用配置：全部来自环境变量 / .env 文件，禁止在代码中硬编码密钥。

用法：
    from knowledge_pilot.config import settings
    settings.deepseek_api_key  # 空字符串表示未配置
"""

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

    @property
    def has_api_key(self) -> bool:
        """是否已配置 LLM 密钥。"""
        return bool(self.deepseek_api_key.strip())


# 模块级单例：应用启动时从环境变量 / .env 读取。
settings = Settings()
