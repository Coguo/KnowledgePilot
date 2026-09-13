"""知识点对话与「可以点亮了吗」的判定（Phase 9 M5）。

两件事：**讲解**（流式）与**判定**（结构化）。判定刻意放在这里而不是 LangGraph 里：
它只依赖「这个知识点的上下文 + 一段对话」，塞进研究图会让那条图多出一个与研究报告
无关的分支，还得为它维护 checkpoint。

## 失败策略与 `evaluate` 刻意相反

`agent/graph.py::evaluate` 解析失败时默认「充分」——那是对的，因为它只是**推进流程**，
判错一次最多多跑一轮研究。这里的判定相反：**解析失败一律不推荐**。点亮是用户的
学习资产（「掌握」这个状态会被记进 DB 与 Markdown），误推荐的代价是污染一份本来
可信的记录，远大于少推荐一次的偏差。所以任何不确定都倒向「不推荐」。
"""

from typing import AsyncIterator

from knowledge_pilot.agent.events import DoneEvent, RecommendEvent, TokenEvent
from knowledge_pilot.learning import notes
from knowledge_pilot.llm.json_utils import parse_json_object
from knowledge_pilot.llm.streaming import stream_capable

EXPLAIN_PROMPT = (
    "你是学习伙伴，正在给用户讲解一个知识点。\n"
    "知识点：{name}\n"
    "类型：{type}\n"
    "一句话说明：{summary}\n"
    "应覆盖的要点：{points}\n"
    "先修知识点：{prereq}\n"
    "所属主题：{topic}\n\n"
    "讲解要求：\n"
    "1. 用简洁口语化的中文，先直接回答用户当前的问题；\n"
    "2. 结合上面的要点循序渐进地展开，一次不要把全部内容倾倒出来；\n"
    "3. 涉及公式或代码时给最小可运行示例；\n"
    "4. 不要评价用户是否已经掌握，也不要宣称「你已经学会了」。"
)

JUDGE_PROMPT = (
    "你在判断一次知识点讲解是否已经讲透，以便决定要不要建议用户标记为「已掌握」。\n"
    "知识点：{name}\n"
    "应覆盖的要点：{points}\n\n"
    "对话记录见随后的用户消息。\n"
    "判断标准：要点是否已经被**实际讲到**（不是「提到了名字」，而是解释清楚了）。\n"
    "严格只输出 JSON（不要任何多余文字）：\n"
    '{{"covered": true 或 false, "confidence": 0 到 1 之间的小数, '
    '"reason": "一句话向用户说明为什么建议点亮（20 字以内）"}}\n'
    "拿不准时 `covered` 一律给 false。"
)

# 带进 prompt 的历史轮数（只影响讲解与判定的上下文，不影响展示）。
HISTORY_LIMIT = 8


async def run_node_chat(
    store,
    node_id: str,
    message: str,
    *,
    llm,
    recommend_enabled: bool = True,
    min_turns: int = 1,
    confidence_threshold: float = 0.7,
    max_tokens: int = 4096,
    history_limit: int = HISTORY_LIMIT,
) -> AsyncIterator[object]:
    """一轮知识点对话：讲解逐字流出 →（够格时）推荐点亮 → `DoneEvent`。

    产出的所有正文都会落两处：DB（`messages`，供刷新恢复）与 Markdown 的
    「讲解记录」（供用户用编辑器阅读）——两者都是**只追加**。

    `max_tokens` 是 4096 而不是一开始的 2048：这个预算在推理模型（`reasoning_content`
    与正文**共用**它）上常常不够一次讲解的思考开销，实测 2048 会被推理吃光——
    那时模型返回的正文是**空字符串**，一个异常都没有（见下面那道闸门）。

    **一个字都没讲到就报错，不落任何记录**（第六轮）。空正文原来是照常落库 + 追加进
    用户 Markdown 的：界面上是一条空气泡、文件里是一个空标题的「讲解记录」，而用户
    完全不知道发生了什么。与提纲的取舍一致（`outline.py`：宁可什么都不写），
    这里宁可抛出去让用户看到一次明确的失败并重试。
    """
    node = store.get_node(node_id)
    if node is None:
        raise ValueError(f"知识点不存在: {node_id}")

    store.add_message(node_id, "user", message)
    turns = store.bump_chat_turns(node_id)

    prompt = _build_prompt(node, store.list_messages(node_id), history_limit=history_limit)

    if not stream_capable(llm):
        text = await llm.complete(prompt, max_tokens=max_tokens)
    else:
        parts: list[str] = []
        async for delta in llm.stream_complete(prompt, max_tokens=max_tokens):
            if not delta:
                continue
            parts.append(delta)
            yield TokenEvent(delta)
        text = "".join(parts)

    if not text.strip():
        # 闸门：空讲解**不是**一条可以落库、落文件的消息。用户已经看到自己的提问了
        # （它在上面就存了），这里让 API 把它变成一帧 `error`，他重试一次即可。
        raise RuntimeError(
            "模型这次没有返回任何内容（可能是推理占满了输出预算），请重试"
        )

    store.add_message(node_id, "assistant", text)
    if node.get("note_path"):
        notes.append_explanation(node["note_path"], title=message, body=text)

    # 三道成本闸门：开关、最少轮数、**只在未学状态**判定（一旦推荐过就不再唠叨，
    # 用户确认掌握后更不会再问）。
    if recommend_enabled and turns >= min_turns:
        state = store.get_node_state(node_id) or {}
        if state.get("status") == "unlearned":
            verdict = await judge_coverage(
                llm, node, store.list_messages(node_id), history_limit=history_limit
            )
            if verdict["covered"] and verdict["confidence"] >= confidence_threshold:
                store.set_node_status(
                    node_id, "recommended",
                    reason=verdict["reason"], confidence=verdict["confidence"],
                )
                yield RecommendEvent(
                    node_id=node_id,
                    reason=verdict["reason"],
                    confidence=verdict["confidence"],
                )

    yield DoneEvent(content=text)


async def judge_coverage(llm, node: dict, messages: list[dict], *, history_limit: int) -> dict:
    """判定「这个知识点讲透了吗」。**任何失败都返回不推荐**（见模块 docstring）。"""
    prompt = [
        {"role": "system", "content": _judge_system(node)},
        {"role": "user", "content": _transcript(messages, history_limit)},
    ]
    try:
        raw = await llm.complete(prompt, response_format={"type": "json_object"})
    except Exception:
        return _NO_RECOMMEND

    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return _NO_RECOMMEND

    return {
        "covered": _as_bool(parsed.get("covered")),
        "confidence": _as_confidence(parsed.get("confidence")),
        "reason": str(parsed.get("reason") or "").strip()[:60],
    }


_NO_RECOMMEND = {"covered": False, "confidence": 0.0, "reason": ""}


def _as_bool(value) -> bool:
    """只认真正的布尔；字符串 "true" 也接受（模型有时会加引号），其余一律 false。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _as_confidence(value) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _build_prompt(node: dict, messages: list[dict], *, history_limit: int) -> list[dict]:
    prompt = [{"role": "system", "content": _explain_system(node)}]
    prompt += [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-history_limit:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return prompt


def _explain_system(node: dict) -> str:
    return EXPLAIN_PROMPT.format(
        name=node["name"],
        type=node.get("type") or "（未标注）",
        summary=node.get("summary") or "（无）",
        points="、".join(node.get("key_points") or []) or "（无预置要点）",
        prereq="、".join(p["name"] for p in node.get("prerequisites") or []) or "无",
        topic=node.get("topic_title") or "（未命名）",
    )


def _judge_system(node: dict) -> str:
    return JUDGE_PROMPT.format(
        name=node["name"],
        points="、".join(node.get("key_points") or []) or "（无预置要点）",
    )


def _transcript(messages: list[dict], history_limit: int) -> str:
    lines = [
        f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}"
        for m in messages[-history_limit:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return "\n\n".join(lines) or "（还没有对话）"
