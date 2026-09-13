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


@dataclass
class KgEvent:
    """Phase 5：知识图谱构建结果（抽取的实体/关系数 + 命中查询的三角组数）。"""

    entities: int
    relations: int
    found_triples: int


# ---- Phase 9 新增事件 ----------------------------------------------------


@dataclass
class ErrorEvent:
    """本轮请求以异常收尾（message 已脱敏，可直接展示给用户）。

    存在的理由：`api/main.py::event_stream` 此前没有 except，异常直接穿出异步生成器
    → 客户端拿到 HTTP 200 + 被截断的 body → 前端一个错误都不显示。用户因此无法区分
    「模型失败了」与「还在跑」。现在后端把异常转成本事件，前端能明确报错。
    """

    message: str


@dataclass
class GraphReadyEvent:
    """Phase 9：学习主题的图谱已生成落库，可以开始学习。

    nodes/edges 为计数（前端真正要的整图走 GET /api/learning/topics/{id} 纯读拉取，
    不塞进事件流——否则大图会把 SSE 帧撑成几百 KB）。
    """

    topic_id: str
    nodes: int
    edges: int
    degraded: bool  # True = LLM 抽取失败，已降级为标题线性路径


@dataclass
class NodesEvent:
    """学习侧生成：**直接从资料抽出的知识点**（不经「先写一份报告再从中抽」）。

    为什么需要一个新事件而不是复用 `DoneEvent`：`DoneEvent.content` 的契约是
    「一段给人读的正文」（对话页直接把它当回答渲染），而这里要传的是**结构化数据**，
    给机器建图用。混进同一个字段，调用方就只能靠「正文看起来像不像 JSON」来猜。

    `summary` 是一句话结论：它顶替了原来「报告第一句」的位置（主题摘要 + 记忆召回
    的展示行），所以报告没了也要有东西可填。
    """

    nodes: list[dict]
    summary: str = ""


@dataclass
class RecommendEvent:
    """Phase 9：系统认为某个知识点已讲透，**推荐**点亮（等用户确认，不自动置掌握）。

    `reason` 是给用户看的判定理由（「讲清了切分粒度与重叠窗口」），不是给机器读的；
    用户据此判断该不该点确认——所以它必须是自然语言，不能是 "covered=true"。
    """

    node_id: str
    reason: str
    confidence: float
