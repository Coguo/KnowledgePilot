"""测试共享的 Fake LLM：脚本化返回，记录调用，全程不联网。"""

from knowledge_pilot.llm.client import StreamChunk


class FakeChatClient:
    """脚本化 LLM：按轮次返回预设内容 / 工具调用。

    script: list of (content_parts: list[str], tool_calls: list[dict])
    超过轮次后重复最后一个条目（用于测试循环兜底）。
    """

    model = "fake"

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list[list[dict]] = []

    async def stream_chat(self, messages, tools=None):
        # 存副本：调用方后续还会往同一个 list 追加消息，不能存引用。
        self.seen_messages.append(list(messages))
        self.seen_tools.append(tools)

        content_parts, tool_calls = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        for part in content_parts:
            yield StreamChunk(content_delta=part)

        for index, tc in enumerate(tool_calls):
            # 名称一次到位、参数分两次到达，专门验证增量累加逻辑。
            yield StreamChunk(tool_call_delta={
                "index": index, "id": f"call_{index}", "name": tc["name"], "arguments": None,
            })
            mid = len(tc["arguments"]) // 2
            yield StreamChunk(tool_call_delta={
                "index": index, "id": None, "name": None, "arguments": tc["arguments"][:mid],
            })
            yield StreamChunk(tool_call_delta={
                "index": index, "id": None, "name": None, "arguments": tc["arguments"][mid:],
            })
