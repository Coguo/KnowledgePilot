"""Task Success 判定：确定性 rubric（默认/离线）+ DeepSeek LLM judge（--real）。

口径（与 plan / docs 一致）：
- **RubricJudge**：确定性、零 LLM、离线/默认。task_success = 报告对 must_include 的覆盖度
  是否达到阈值（默认 1.0）。判的是"期望要点到没到齐"，不判文笔/相关性。
- **DeepSeekJudge**：--real 的质量维度。让真实 LLM 看完整报告，按 item.aspects 逐条给
  是与否，再综合 task_success。回答"这报告真的算成功吗"——rubric 判不出而 LLM 能判的
  部分（引用合理性、是否答非所问）。任何解析失败**回退 rubric** 并记 reason=
  "judge_fallback"，绝不崩 run（与 planner/evaluate/kg 的失败回退同一纪律）。

DeepSeek json_object 硬性要求：prompt 必须包含单词 "json"，否则 HTTP 400（同 planner/
evaluate/kg 的 prompt 写法）。
"""

from dataclasses import dataclass, field
from typing import Protocol

from knowledge_pilot.agent.eval.dataset import AgentItem
from knowledge_pilot.agent.eval.metrics import coverage, passes_threshold
from knowledge_pilot.llm.json_utils import parse_json_object

JUDGE_SYSTEM_PROMPT = (
    "你是研究质量评测员。阅读用户给出的研究报告，判定它是否成功回答了研究任务，"
    "并对每个评分维度逐条给判定。\n"
    "请以 json 格式回答（输出一个 json object，不要任何多余文字），格式为：\n"
    '{"task_success": 0或1, "aspects": {"维度名": true或false}, "reason": "一句话理由"}\n'
    "要求：task_success=1 当且仅当报告确实回答了研究问题且要点覆盖完整。"
)


@dataclass(frozen=True)
class JudgeVerdict:
    """一次评分结果。source: rubric（确定性）/ llm（真实模型）/ rubric_fallback。"""

    task_success: float
    reason: str
    aspects: dict[str, bool] = field(default_factory=dict)
    source: str = "rubric"


class Judge(Protocol):
    """Task Success 判定器。llm_based=True 时 runner 把 task_success 记入 judge 列。"""

    llm_based: bool

    async def score(self, item: AgentItem, report: str) -> JudgeVerdict: ...


def _rubric_score(terms: list[str], report: str, threshold: float) -> tuple[float, str]:
    """同步 rubric 打分核心：返回 (task_success, reason)。"""
    if not terms:
        return 1.0, "无覆盖词要求（rubric 通过）"
    hit = sum(1 for t in terms if coverage([t], report) >= 1.0)
    cov = hit / len(terms)
    ok = passes_threshold(cov, threshold)
    return ok, f"rubric 覆盖 {hit}/{len(terms)}（threshold={threshold}）"


class RubricJudge:
    """确定性 rubric：覆盖 must_include 词（metrics.coverage + passes_threshold）。"""

    llm_based = False

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold

    async def score(self, item: AgentItem, report: str) -> JudgeVerdict:
        terms = [t for t in item.must_include if isinstance(t, str)]
        ok, reason = _rubric_score(terms, report, self.threshold)
        return JudgeVerdict(task_success=ok, reason=reason, source="rubric")


class DeepSeekJudge:
    """LLM judge：读报告按 aspects 逐条判，综合 task_success。

    llm 需满足 LLMClient.complete（--real 用独立 ChatClient；测试注入脚本化 client）。
    报告截断到 max_report_chars 防超长；解析失败回退 rubric（source="rubric_fallback"）。
    """

    llm_based = True

    def __init__(
        self,
        llm,
        *,
        max_report_chars: int = 6000,
        rubric_threshold: float = 1.0,
    ) -> None:
        self.llm = llm
        self.max_report_chars = max_report_chars
        self.rubric = RubricJudge(rubric_threshold)

    async def score(self, item: AgentItem, report: str) -> JudgeVerdict:
        report = (report or "")[: self.max_report_chars]
        aspects = {a.name: a.question for a in item.aspects}
        dims_lines = "\n".join(
            f"- {name}：{question}" for name, question in aspects.items()
        ) or "（无附加维度）"
        terms_line = "、".join(item.must_include) if item.must_include else "（无）"
        prompt = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"研究任务：{item.query}\n"
                    f"期望覆盖要点：{terms_line}\n"
                    f"评分维度：\n{dims_lines}\n"
                    f"研究报告（已截断）：\n{report}\n"
                ),
            },
        ]
        try:
            raw = await self.llm.complete(
                prompt, response_format={"type": "json_object"}
            )
        except Exception:
            return self._fallback(item, report)

        parsed = parse_json_object(raw)
        if not isinstance(parsed, dict):
            return self._fallback(item, report)

        ts = parsed.get("task_success")
        aspects_raw = parsed.get("aspects")
        aspects_out = {
            name: bool((aspects_raw or {}).get(name)) for name in aspects
        }
        return JudgeVerdict(
            task_success=1.0 if ts is True or ts == 1 else 0.0,
            reason=str(parsed.get("reason") or ""),
            aspects=aspects_out,
            source="llm",
        )

    def _fallback(self, item: AgentItem, report: str) -> JudgeVerdict:
        terms = [t for t in item.must_include if isinstance(t, str)]
        ok, reason = _rubric_score(terms, report, self.rubric.threshold)
        return JudgeVerdict(
            task_success=ok,
            reason="judge_fallback（LLM judge 结果不可解析）→ " + reason,
            source="rubric_fallback",
        )
