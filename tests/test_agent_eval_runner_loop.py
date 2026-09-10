"""runner loop 档冒烟测试（纯 stdlib，无需 langgraph——本地/CI 沙箱即可跑）。

run_agent_eval 只驱动 loop 变体时不触 graph driver 懒导入，能在无 langgraph 环境
端到端验证：聚合口径、error_rate 承接崩溃轴、selection 只查"该调的调了没"
（未知工具的崩溃归 error_rate，不进 selection——plan 口径）。
"""

import asyncio

import pytest

from knowledge_pilot.agent.eval.dataset import load_dataset
from knowledge_pilot.agent.eval.offline import make_offline_components
from knowledge_pilot.agent.eval.runner import run_agent_eval

FIXTURE = "tests/fixtures/eval_agent/small.json"


def _run_loop():
    return asyncio.run(
        run_agent_eval(
            load_dataset(FIXTURE),
            components=make_offline_components(),
            variants=["loop"],
        )
    )


def test_loop_row_aggregates_and_error_rate():
    results = _run_loop()
    assert len(results) == 1
    row = results[0]
    assert row.variant == "loop"
    assert row.n_items == 4
    # 三条跑通（ideal/iterate/memory）judge=1；mcp_only 条目在 loop 无 mcp 工具 → ValueError
    assert row.task_success == 0.75
    assert row.error_rate == 0.25
    assert row.coverage == 0.75
    # 脚本复用 gold 参数 → selection/argument 均满；崩溃轴已落 error_rate
    assert row.tool_selection == 1.0
    assert row.tool_argument == 1.0
    # loop 从不调 complete；平均 stream 轮次 = (2+2+1+2)/4
    assert row.complete_calls_avg == 0.0
    assert row.stream_calls_avg == pytest.approx(1.75)  # (2+2+1+2)/4
    assert row.tokens_avg > 0
    assert row.judge == "rubric"
