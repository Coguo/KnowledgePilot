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


# ---- Phase 3（LangGraph 编排）新增事件 ----------------------------------


@dataclass
class PlanEvent:
    """Planner 节点生成的研究计划（步骤列表，每项含 title/question/purpose）。"""

    plan: list[dict]


@dataclass
class StatusEvent:
    """研究流程的阶段切换/进度提示（如「第 2 轮研究」）。"""

    message: str


@dataclass
class EvalEvent:
    """Evaluate 节点对「信息是否充分」的判定结果。"""

    sufficient: bool
    reason: str
    iteration: int


@dataclass
class MemoryEvent:
    """Phase 4：开始研究时召回了多少条历史研究记录（供规划参考复用）。"""

    found: int
