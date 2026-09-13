"""Phase 9 M5：知识点对话与点亮判定。

判定部分最要紧的是**失败倒向哪边**：`evaluate` 解析失败默认「充分」（推进流程），
这里必须反过来默认「不推荐」——点亮是用户的学习资产，误推荐会污染一份本来可信的
记录。下面每个失败分支都有专门的断言。
"""

import json

import pytest

from knowledge_pilot.agent.events import DoneEvent, RecommendEvent, TokenEvent
from knowledge_pilot.learning import create_learning_store
from knowledge_pilot.learning import notes as notes_mod
from knowledge_pilot.learning import service, session

GRAPH = {
    "nodes": [
        {"name": "文本切分", "type": "技术", "summary": "把长文档切成块",
         "key_points": ["定长切分", "重叠窗口"], "depth": 0, "order_index": 0},
        {"name": "向量检索", "type": "技术", "summary": "近邻搜索",
         "key_points": ["embedding"], "depth": 1, "order_index": 1,
         "prerequisites": ["文本切分"]},
    ],
    "edges": [{"source": "文本切分", "target": "向量检索", "relation": "前置"}],
}

VERDICT_OK = json.dumps(
    {"covered": True, "confidence": 0.9, "reason": "讲清了切分粒度与重叠窗口"},
    ensure_ascii=False,
)


class _StreamingLLM:
    """有 `stream_complete`（生产路径）：讲解逐段流出，`complete` 留给判定。"""

    def __init__(self, *, deltas=("把长文档", "切成块。"), judge=VERDICT_OK):
        self.deltas = list(deltas)
        self.judge = judge
        self.complete_calls = 0
        self.stream_calls = 0
        self.seen_prompts: list[list[dict]] = []

    async def stream_complete(self, messages, *, max_tokens=None, response_format=None):
        self.stream_calls += 1
        self.seen_prompts.append(list(messages))
        for delta in self.deltas:
            yield delta

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        self.complete_calls += 1
        self.seen_prompts.append(list(messages))
        if isinstance(self.judge, BaseException):
            raise self.judge
        return self.judge


class _PlainLLM:
    """**没有** `stream_complete`（Fake / 评测客户端走的降级路径）。"""

    def __init__(self, *, answers=("一次性给出的讲解。",), judge=VERDICT_OK):
        self.answers = list(answers)
        self.judge = judge
        self.calls: list = []

    async def complete(self, messages, *, max_tokens=None, response_format=None):
        self.calls.append(list(messages))
        # 判定那次带 response_format（json_object），据此区分两种调用
        if response_format:
            return self.judge
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


@pytest.fixture
def env(tmp_path):
    """真实落盘的 store + 正文目录（不 mock 存储层）。"""
    store = create_learning_store(str(tmp_path / "learning.db"))
    topic = store.create_topic("RAG 的 chunking 策略")
    service.persist_graph(
        store, topic["id"], GRAPH,
        notes_dir=str(tmp_path / "knowledge"), report="## 文本切分\n\n正文\n",
    )
    node = store.get_graph(topic["id"])["nodes"][0]
    node["topic_title"] = topic["title"]
    yield store, node
    store.close()


async def _drain(agen):
    return [event async for event in agen]


def _types(events):
    return [type(e).__name__ for e in events]


# ---- 讲解 -------------------------------------------------------------


async def test_streaming_explanation_emits_tokens_then_done(env):
    store, node = env
    llm = _StreamingLLM()
    events = await _drain(session.run_node_chat(store, node["id"], "什么是切分？", llm=llm))

    tokens = [e.content for e in events if isinstance(e, TokenEvent)]
    done = [e for e in events if isinstance(e, DoneEvent)]
    assert tokens == ["把长文档", "切成块。"]
    assert len(done) == 1
    assert done[0].content == "".join(tokens)  # 不变量：全文 == token 拼接


async def test_plain_client_falls_back_without_token_events(env):
    """没有 stream_complete 时不补发一次性 token——正文由 DoneEvent 兜底渲染。"""
    store, node = env
    llm = _PlainLLM()
    events = await _drain(session.run_node_chat(store, node["id"], "讲讲切分", llm=llm))

    assert not [e for e in events if isinstance(e, TokenEvent)]
    assert [e for e in events if isinstance(e, DoneEvent)][0].content == "一次性给出的讲解。"


async def test_chat_persists_both_turns_and_bumps_counter(env):
    store, node = env
    await _drain(session.run_node_chat(store, node["id"], "什么是切分？", llm=_StreamingLLM()))

    messages = store.list_messages(node["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "什么是切分？"
    assert messages[1]["content"] == "把长文档切成块。"
    assert store.get_node_state(node["id"])["chat_turns"] == 1


async def test_explanation_is_appended_to_the_markdown_note(env):
    store, node = env
    await _drain(session.run_node_chat(store, node["id"], "什么是切分？", llm=_StreamingLLM()))

    text = notes_mod.read_text(node["note_path"])
    assert "把长文档切成块。" in text
    assert notes_mod.USER_SECTION in text
    assert text.index("把长文档切成块。") < text.index(notes_mod.USER_SECTION)  # 用户区仍在末尾


async def test_history_is_fed_back_into_the_prompt(env):
    """第二轮要带上第一轮——否则「刚才那个呢」这类追问无从回答。"""
    store, node = env
    await _drain(session.run_node_chat(store, node["id"], "第一个问题", llm=_StreamingLLM()))
    llm = _StreamingLLM()
    await _drain(session.run_node_chat(store, node["id"], "那重叠窗口呢？", llm=llm))

    prompt = llm.seen_prompts[0]
    assert [m["role"] for m in prompt] == ["system", "user", "assistant", "user"]
    assert prompt[-1]["content"] == "那重叠窗口呢？"
    assert "第一个问题" in prompt[1]["content"]


async def test_unknown_node_raises(env):
    store, _ = env
    with pytest.raises(ValueError, match="知识点不存在"):
        await _drain(session.run_node_chat(store, "nope", "hi", llm=_StreamingLLM()))


# ---- 空讲解：宁可报错，不落空记录（第六轮） ----------------------------


def _note_after(store, node):
    """正文文件里「讲解记录」之后的内容长度（用来断言「一个字节都没追加」）。"""
    return len(notes_mod.read_text(node["note_path"]))


async def test_empty_streamed_explanation_raises_instead_of_persisting(env):
    """推理模型吃光预算时正文是**空字符串**且不报错 —— 那不该变成一条空消息。

    用户看到一个空气泡、Markdown 里多一条空标题的「讲解记录」，而界面上没有任何
    失败提示。现在它抛出去，由 API 变成一帧 `error`（用户重试一次即可）。
    """
    store, node = env
    llm = _StreamingLLM(deltas=("", ""))
    before = _note_after(store, node)

    with pytest.raises(RuntimeError, match="没有返回任何内容"):
        await _drain(session.run_node_chat(store, node["id"], "讲讲切分", llm=llm))

    assert [m["role"] for m in store.list_messages(node["id"])] == ["user"]  # 没落空回答
    assert _note_after(store, node) == before  # 文件一个字节都没动
    assert llm.complete_calls == 0  # 也没白烧一次判定


async def test_blank_only_explanation_counts_as_empty(env):
    """只有空白（`"\\n"` / 空格）也算没讲到——`text.strip()` 才是判据。"""
    store, node = env
    llm = _StreamingLLM(deltas=("\n", "   "))
    with pytest.raises(RuntimeError, match="没有返回任何内容"):
        await _drain(session.run_node_chat(store, node["id"], "讲讲切分", llm=llm))


async def test_empty_plain_explanation_raises_too(env):
    """没有 `stream_complete` 的那条路（eval / Fake 客户端）同样不许落空记录。"""
    store, node = env
    llm = _PlainLLM(answers=("",))
    with pytest.raises(RuntimeError, match="没有返回任何内容"):
        await _drain(session.run_node_chat(store, node["id"], "讲讲切分", llm=llm))
    assert [m["role"] for m in store.list_messages(node["id"])] == ["user"]


async def test_explanation_budget_is_wide_enough_for_a_reasoning_model(env):
    """预算默认 4096：2048 实测会被推理吃光（那时正文是空的、且不报错）。

    这条防的是「有人觉得 4096 太贵顺手改回 2048」——改回去不会让任何测试变红，
    只会让生产上的讲解**偶尔变成一条空气泡**。
    """
    store, node = env
    seen: list = []
    llm = _StreamingLLM()

    async def _spy(messages, *, max_tokens=None, response_format=None):
        seen.append(max_tokens)
        for delta in llm.deltas:
            yield delta

    llm.stream_complete = _spy
    await _drain(session.run_node_chat(store, node["id"], "讲讲切分", llm=llm))
    assert seen == [4096]


# ---- 判定与推荐 -------------------------------------------------------


async def test_recommends_when_judge_says_covered(env):
    store, node = env
    events = await _drain(session.run_node_chat(store, node["id"], "讲讲", llm=_StreamingLLM()))

    recommend = [e for e in events if isinstance(e, RecommendEvent)]
    assert len(recommend) == 1
    assert recommend[0].reason == "讲清了切分粒度与重叠窗口"
    assert recommend[0].confidence == 0.9
    assert store.get_node_state(node["id"])["status"] == "recommended"
    assert store.get_node_state(node["id"])["recommend_reason"] == "讲清了切分粒度与重叠窗口"


async def test_low_confidence_is_not_recommended(env):
    """置信度不到阈值不推荐——半懂不算掌握。"""
    store, node = env
    llm = _StreamingLLM(judge=json.dumps({"covered": True, "confidence": 0.4, "reason": "还行"}))
    events = await _drain(session.run_node_chat(store, node["id"], "讲讲", llm=llm))

    assert not [e for e in events if isinstance(e, RecommendEvent)]
    assert store.get_node_state(node["id"])["status"] == "unlearned"


async def test_not_covered_is_not_recommended(env):
    store, node = env
    llm = _StreamingLLM(judge=json.dumps({"covered": False, "confidence": 0.95, "reason": ""}))
    events = await _drain(session.run_node_chat(store, node["id"], "讲讲", llm=llm))
    assert not [e for e in events if isinstance(e, RecommendEvent)]


@pytest.mark.parametrize("bad", ["模型没说人话", "", "not json at all", None])
async def test_unparseable_verdict_never_recommends(env, bad):
    """**与 `evaluate` 相反**：解析失败默认不推荐，绝不默认「讲透了」。"""
    store, node = env
    llm = _StreamingLLM(judge=bad)
    events = await _drain(session.run_node_chat(store, node["id"], "讲讲", llm=llm))
    assert not [e for e in events if isinstance(e, RecommendEvent)]
    assert store.get_node_state(node["id"])["status"] == "unlearned"


async def test_judge_exception_never_recommends(env):
    store, node = env
    llm = _StreamingLLM(judge=RuntimeError("判定服务挂了"))
    events = await _drain(session.run_node_chat(store, node["id"], "讲讲", llm=llm))
    assert [e for e in events if isinstance(e, DoneEvent)]  # 讲解本身照常完成
    assert not [e for e in events if isinstance(e, RecommendEvent)]


async def test_recommendation_is_not_repeated_on_later_turns(env):
    """**再聊不再重复判定**：已进入 recommended 就不再花判定调用。"""
    store, node = env
    await _drain(session.run_node_chat(store, node["id"], "第一轮", llm=_StreamingLLM()))

    llm = _StreamingLLM()
    events = await _drain(session.run_node_chat(store, node["id"], "第二轮", llm=llm))
    assert llm.complete_calls == 0  # 一次判定都没做
    assert not [e for e in events if isinstance(e, RecommendEvent)]


async def test_mastered_node_is_never_judged_again(env):
    store, node = env
    store.set_node_status(node["id"], "mastered")

    llm = _StreamingLLM()
    await _drain(session.run_node_chat(store, node["id"], "再讲讲", llm=llm))
    assert llm.complete_calls == 0


async def test_recommend_disabled_skips_judgement_entirely(env):
    store, node = env
    llm = _StreamingLLM()
    events = await _drain(
        session.run_node_chat(
            store, node["id"], "讲讲", llm=llm, recommend_enabled=False
        )
    )
    assert llm.complete_calls == 0
    assert not [e for e in events if isinstance(e, RecommendEvent)]


async def test_min_turns_gate_skips_judgement_until_enough_turns(env):
    """至少聊够 N 轮才判定——省掉没必要的调用。"""
    store, node = env
    llm = _StreamingLLM()
    await _drain(session.run_node_chat(store, node["id"], "第一轮", llm=llm, min_turns=2))
    assert llm.complete_calls == 0  # 才 1 轮

    llm = _StreamingLLM()
    events = await _drain(session.run_node_chat(store, node["id"], "第二轮", llm=llm, min_turns=2))
    assert llm.complete_calls == 1
    assert [e for e in events if isinstance(e, RecommendEvent)]


async def test_custom_threshold_is_respected(env):
    store, node = env
    llm = _StreamingLLM(judge=json.dumps({"covered": True, "confidence": 0.75, "reason": "可以"}))
    events = await _drain(
        session.run_node_chat(
            store, node["id"], "讲讲", llm=llm, confidence_threshold=0.5
        )
    )
    assert [e for e in events if isinstance(e, RecommendEvent)]


# ---- judge_coverage 单元 ---------------------------------------------


async def test_judge_coerces_boolean_like_strings(env):
    store, node = env
    llm = _StreamingLLM(judge='{"covered": "true", "confidence": "0.8", "reason": "ok"}')
    verdict = await session.judge_coverage(llm, node, [], history_limit=8)
    assert verdict == {"covered": True, "confidence": 0.8, "reason": "ok"}


async def test_judge_clamps_confidence_into_range(env):
    store, node = env
    llm = _StreamingLLM(judge='{"covered": true, "confidence": 5, "reason": "x"}')
    assert (await session.judge_coverage(llm, node, [], history_limit=8))["confidence"] == 1.0
    llm = _StreamingLLM(judge='{"covered": true, "confidence": -3, "reason": "x"}')
    assert (await session.judge_coverage(llm, node, [], history_limit=8))["confidence"] == 0.0


async def test_judge_treats_non_boolean_covered_as_false(env):
    """`covered` 给了个莫名其妙的字符串 → 当成没讲到（宁可少推荐）。"""
    store, node = env
    llm = _StreamingLLM(judge='{"covered": "也许吧", "confidence": 0.99, "reason": "x"}')
    assert (await session.judge_coverage(llm, node, [], history_limit=8))["covered"] is False


async def test_judge_asks_for_json_object_mode(env):
    """DeepSeek 的 json_object 模式要求请求体带 response_format，且 prompt 含 "json"。"""
    store, node = env
    seen = {}

    class _Recording:
        async def complete(self, messages, *, max_tokens=None, response_format=None):
            seen["rf"] = response_format
            seen["text"] = json.dumps(messages, ensure_ascii=False)
            return VERDICT_OK

    await session.judge_coverage(_Recording(), node, [], history_limit=8)
    assert seen["rf"] == {"type": "json_object"}
    assert "json" in seen["text"].lower()


async def test_judge_reason_is_length_capped(env):
    store, node = env
    llm = _StreamingLLM(
        judge=json.dumps({"covered": True, "confidence": 0.9, "reason": "字" * 200})
    )
    assert len((await session.judge_coverage(llm, node, [], history_limit=8))["reason"]) == 60


async def test_judge_transcript_includes_the_conversation(env):
    store, node = env
    seen = {}

    class _Recording:
        async def complete(self, messages, *, max_tokens=None, response_format=None):
            seen["user"] = messages[-1]["content"]
            return VERDICT_OK

    history = [
        {"role": "user", "content": "什么是切分"},
        {"role": "assistant", "content": "把长文档切成块"},
    ]
    await session.judge_coverage(_Recording(), node, history, history_limit=8)
    assert "用户：什么是切分" in seen["user"]
    assert "助手：把长文档切成块" in seen["user"]


async def test_judge_empty_history_says_so(env):
    store, node = env
    seen = {}

    class _Recording:
        async def complete(self, messages, *, max_tokens=None, response_format=None):
            seen["user"] = messages[-1]["content"]
            return VERDICT_OK

    await session.judge_coverage(_Recording(), node, [], history_limit=8)
    assert "还没有对话" in seen["user"]


def test_explain_prompt_carries_node_context(env):
    _, node = env
    system = session._explain_system(node)
    assert "文本切分" in system
    assert "定长切分" in system and "重叠窗口" in system
    assert "RAG 的 chunking 策略" in system  # 所属主题
