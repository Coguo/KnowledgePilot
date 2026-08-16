"""Agent 循环对外发出的事件。

事件流是引擎与 UI 之间的唯一契约：Web 层映射为 SSE，
未来的桌面版直接消费同一套事件（或同一 HTTP API）。
"""

from dataclasses import dataclass


@dataclass
class TokenEvent:
    """LLM 输出的一段文本增量。"""

    content: str


@dataclass
class ToolCallEvent:
    """Agent 决定调用某个工具。"""

    name: str
    arguments: str  # JSON 字符串


@dataclass
class ToolResultEvent:
    """工具执行完成。"""

    name: str
    summary: str  # 给 UI 展示的一句话摘要


@dataclass
class DoneEvent:
    """一次会话结束，携带最终完整答案。"""

    content: str
