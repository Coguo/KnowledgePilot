"""Agent 核心：手写 tool-calling 循环，与 UI/传输层完全解耦。"""

from knowledge_pilot.agent.loop import run_research

__all__ = ["run_research"]
