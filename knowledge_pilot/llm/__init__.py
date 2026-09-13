"""LLM 模块：OpenAI 兼容客户端（ChatClient）与统一访问层（Model Gateway）。

懒导出（PEP 562）：import knowledge_pilot.llm 及其子模块时不再触发 openai——
`StreamChunk` / `LLMClient`（协议）在 `llm/protocol.py`（纯 stdlib），`ChatClient`
与 Model Gateway 的实现只在真正被访问时才 import。这样纯 stdlib / 离线模块
（errors / gateway / providers）可在未装 openai 的环境直接 import（A 轨测试依赖）。
全仓消费方均直接 import 子模块（llm.client / llm.protocol），不依赖本包顶层导出。
"""

__all__ = ["ChatClient", "LLMClient", "StreamChunk", "ModelGateway", "build_llm_client"]


def __getattr__(name):
    if name in ("ChatClient", "LLMClient", "StreamChunk"):
        from knowledge_pilot.llm.client import ChatClient, LLMClient, StreamChunk

        return {
            "ChatClient": ChatClient,
            "LLMClient": LLMClient,
            "StreamChunk": StreamChunk,
        }[name]
    if name in ("ModelGateway", "build_llm_client"):
        from knowledge_pilot.llm.gateway import ModelGateway, build_llm_client

        return {
            "ModelGateway": ModelGateway,
            "build_llm_client": build_llm_client,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
