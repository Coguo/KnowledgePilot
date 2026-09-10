"""Agent Evaluation：图级/全栈端到端评测（规格 §13）+ CLI。

回答「图 + 记忆 + KG + MCP 相比单轮手写循环是否真的更好」——用一组带金标准的
端到端研究任务，在五档变体（loop / graph / graph+memory / graph+kg / all）上量化：

- **Task Success**：默认确定性 rubric（报告覆盖词）+ `--real` DeepSeek LLM judge。
- **Tool Selection / Argument Accuracy**：工具调用轨迹与期望比对。
- **System**：Latency（P50/P95，graph 弃首 warmup）、Token 启发式成本、complete/stream
  调用次数、error rate、memory/kg 事件落点。

诚实边界（与 rag/eval 同口径）：**离线 LLM 是脚本化的**——五档变体在报告质量/覆盖上
不具区分度，离线区分的是**机制 + 系统指标**（脚本扰动下能否完成、工具轨迹、调用计数、
事件落点、error）；**质量与「哪个变体真的更好」的答案在 `--real`**（真实 DeepSeek 跑 +
LLM judge 判逐条质量）。

CLI 用法：
    python -m knowledge_pilot.agent.eval --dataset tests/fixtures/eval_agent/small.json
    python -m knowledge_pilot.agent.eval --dataset ... --variant loop,graph+memory,all --real

import-safe：本包不 import langgraph / mcp（agent 包与 graph 均懒导出/显式子模块导入），
未装 langgraph 的环境可正常 import 离线组件并跑 A 轨单测（runner 只在驱动 graph 档时才
懒导入 langgraph）。
"""

from knowledge_pilot.agent.eval.dataset import (
    KNOWN_VARIANT_NAMES,
    VALID_PROFILES,
    AgentAspect,
    AgentDataset,
    AgentItem,
    GoldToolCall,
    SeedRun,
    load_dataset,
)
from knowledge_pilot.agent.eval.judge import DeepSeekJudge, JudgeVerdict, RubricJudge
from knowledge_pilot.agent.eval.metrics import (
    coverage,
    mean,
    passes_threshold,
    tool_argument_accuracy,
    tool_selection_accuracy,
)
from knowledge_pilot.agent.eval.offline import (
    ALL_VARIANTS,
    Variant,
    build_script_plan,
    make_offline_components,
)
from knowledge_pilot.agent.eval.runner import (
    ItemOutcome,
    VariantResult,
    format_results_table,
    run_agent_eval,
)

# real.py（真实组件）不在此 re-export：它需要 DEEPSEEK_API_KEY，由 CLI `--real`
# 与 B 轨测试按需懒加载，避免无 key 环境 import 即报错。

__all__ = [
    # dataset
    "AgentAspect",
    "AgentDataset",
    "AgentItem",
    "GoldToolCall",
    "SeedRun",
    "KNOWN_VARIANT_NAMES",
    "VALID_PROFILES",
    "load_dataset",
    # metrics / judge
    "RubricJudge",
    "DeepSeekJudge",
    "JudgeVerdict",
    "coverage",
    "mean",
    "passes_threshold",
    "tool_argument_accuracy",
    "tool_selection_accuracy",
    # offline / runner
    "ALL_VARIANTS",
    "Variant",
    "ItemOutcome",
    "VariantResult",
    "build_script_plan",
    "make_offline_components",
    "run_agent_eval",
    "format_results_table",
]
