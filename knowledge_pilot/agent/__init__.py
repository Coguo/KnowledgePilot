"""Agent 核心：手写 tool-calling 循环 + LangGraph 编排，与 UI/传输层完全解耦。

懒导出（PEP 562）：import knowledge_pilot.agent 或其子包时不再触发 langgraph——
run_research / run_research_graph 只在真正被访问时才 import 对应子模块。
这样纯 stdlib 模块（如 agent/eval 的 dataset/metrics/judge）可在未装 langgraph 的
环境直接 import（Phase 7 Agent Evaluation 的 A 轨离线单测依赖这一点）。
全仓消费方均直接 import 子模块（agent.loop / agent.graph），不依赖本包顶层导出。
"""

__all__ = ["run_research", "run_research_graph"]


def __getattr__(name):
    if name == "run_research":
        from knowledge_pilot.agent.loop import run_research

        return run_research
    if name == "run_research_graph":
        from knowledge_pilot.agent.graph import run_research_graph

        return run_research_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
