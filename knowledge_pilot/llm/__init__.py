"""LLM 客户端：封装 OpenAI 兼容接口（默认 DeepSeek），支持流式与工具调用。"""

from knowledge_pilot.llm.client import ChatClient, LLMClient, StreamChunk

__all__ = ["ChatClient", "LLMClient", "StreamChunk"]
