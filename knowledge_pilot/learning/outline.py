"""一个知识点的「提纲」——**打开节点时按需生成**。

## 为什么要有独立的一段提纲

`## 要点` 是**结论**（这个知识点是什么），不是**清单**（它由哪几块组成）。用户走查
时报的正是这件事：拿到图、拿到要点、也拿到了研究报告，依然不知道从哪儿下手——因为
报告是**按主题**写的，而学习是**按知识点**发生的，中间那一步「这个知识点里有哪些
东西要弄明白」从来没有人写过。提纲就是那一步。

## 每一条是小点，不是句子（走查反馈 ②）

第一版写成「说清分块粒度为什么会改变检索召回」那样的一句话，读起来像任务清单：每一
条都得先读懂，才知道该学什么。用户要的是**小点**——「有哪些分块方法」「各自的切分
规则」「会遇到什么问题」「怎么解决」这种 8~14 字的碎片，一眼扫过去就能看出自己漏了
哪一块。所以 prompt 把颗粒度、长度和覆盖次序（种类 → 做法 → 问题 → 处理）都写死，
并在 `ITEM_LIMIT` 上收紧到 30 字作安全网。次序仍然有意义，`notes.outline_block` 才
用有序列表落盘。

## 成本策略：按需 + 一次 + 不留半成品

- **按需**：不在 `persist_graph` 里批量生成。那会让每个节点的成本翻倍，而多数
  节点用户永远不会点开；
- **一次**：产物写进笔记文件的 `## 提纲` 段，`notes.read_outline` 判重，所以一个
  知识点一辈子只花一次调用（重开节点是纯读 GET，零 LLM）；
- **不留半成品**：任何失败（模型挂了 / 返回不是 JSON / 一条都取不出来）都返回
  `[]`，**一个字节都不写**，下次打开自动重试。

第三条与 `service.build_graph_from_report` 的降级链是**相反**的取舍，理由不是风格：
降级链的产物是**立刻显示**在屏幕上的（线性章节路径），有总比没有好，用户看到提示
还能重新生成；而提纲会被**永久写进用户自己的文件**，而 `notes.insert_outline` 从不
覆写——写坏了没有第二次机会。所以这里不降级、不留占位、宁可让用户看到一次「重试」。

「不留半成品」指的是**不留写坏的内容**（半句话、一条被截断的条目、来路不明的占位），
不是「条数必须够」。第七轮加的 `salvage_json` 救回来的恰好是**完整的条目**，只是比
要求的少几条（`clean_outline` 仍然照 `ITEM_LIMIT` / `MAX_ITEMS` 洗一遍）——那不是
半成品，是「这份清单短了一点」，比一个字都没有有用。真正截断在条目中间的那一条会被
救回过程整个丢掉，不会被写成半句话。
"""

import re

from knowledge_pilot.llm.json_utils import salvage_json
from knowledge_pilot.llm.providers import THINKING_OFF

# 提纲条数。少于 3 条不成清单（那是「要点」的粒度），超过 6 条用户不会读完。
MAX_ITEMS = 6
MIN_USEFUL_ITEMS = 3

# 单条的字符上限。**这是安全网，不是目标** —— 目标是 8~14 字的小点（见 prompt）。
# 模型偶尔会把整段话塞进一条，截断比整条丢掉好——丢一条就是从清单上挖掉一块。
#
# 走查反馈 ② 把它从 80 降到 30：80 字足够塞下一整句话，于是「模型写长句」时截断
# 也救不回来——它看起来仍然是一份合法的提纲，只是每一条都读不动。上限贴着目标
# 定，写长了才会留下那个 `…`。
ITEM_LIMIT = 30

# 带进 prompt 的报告节选上限。报告可能几万字，而提纲只需要「这个知识点在这个主题
# 里的语境」，不需要全文。
EXCERPT_LIMIT = 1600

# 输出预算（第七轮补上）。原来这次调用**没传 max_tokens**，走 provider 默认——而推理
# 模型的 reasoning_content 与正文共用这份额度：实测推理 2571 字 / 正文 88 字，也就是
# 说默认额度里九成以上花在了「想」上，而这里要的只是一份短清单。额度只卡输出、不预留，
# 给宽是免费的保险。
OUTLINE_MAX_TOKENS = 4096

OUTLINE_PROMPT = (
    "你在为一个**完全不了解**某个知识点的人列一份提纲。\n"
    "他刚在知识图谱里点开了这个知识点，还没开始学——他要的既不是结论，也不是"
    "「先弄明白什么」那种一句话的路线，而是**这个知识点由哪几块组成、每一块要弄清什么**："
    "照着这几条看下去，他才知道自己漏了什么。\n\n"
    "要求：\n"
    f"1. 给 {MIN_USEFUL_ITEMS} 到 {MAX_ITEMS} 条，**顺序有意义**；\n"
    "2. **每条都是一个小点**：8~14 字，名词短语或极短问句。写不下就换一条更小的，"
    "不要把它拉长成一句话——提纲是一条条看的，不是读的；\n"
    "3. 按这个次序覆盖下面几类（能覆盖几类就给几条，不必凑数）：\n"
    "   ① 它有哪些种类 / 由哪几部分组成；\n"
    "   ② 每一类各自怎么做、怎么用；\n"
    "   ③ 做的时候会遇到什么问题、坑在哪；\n"
    "   ④ 这些问题怎么处理；\n"
    "4. 只覆盖这一个知识点，不要扩散成整份报告的学习计划；\n"
    "5. 不要自己加编号（序号由排版给出），不要「本知识点」「接下来我们将」这类元话术；\n"
    "6. 用中文。\n\n"
    "风格示例（假设知识点是「手冲咖啡」——只看长度与颗粒度，不要抄内容）：\n"
    '{"outline": ["常见的冲煮方式", "各自的粉水比与水温", "容易翻车的环节", "怎么调整补救"]}\n\n'
    "严格只输出 JSON（不要任何多余文字）：\n"
    '{"outline": ["第一条", "第二条", "第三条"]}'
)


async def generate_outline(
    llm,
    node: dict,
    *,
    topic_title: str = "",
    report: str = "",
    max_items: int = MAX_ITEMS,
) -> list[str]:
    """为一个知识点生成提纲。**永不抛异常**，失败返回 `[]`（= 什么都不写）。

    `llm is None` 也走同一条路——调用方不必先判断有没有模型。

    第七轮两处改动，都是因为实测发现这一处比别处更脆：

    - **补上 `max_tokens`**。原来压根没传，走 provider 默认。实测这次调用
      **推理 2571 字 / 正文 88 字（29 倍）**——推理是答案的 29 倍，而额度是两边共用的，
      越界就是正文为空、返回 `[]`、界面上表现为「这个节点没有提纲」。
    - **关掉思考**（`THINKING_OFF`）。提纲是**结构化的短清单**，不是需要想很久的难题，
      没有理由让推理去占那一半额度（这一处**没有**单独实测过关掉之后的效果；依据是
      同类的抽取步骤实测 reasoning 由 19474 字降到 0、`finish_reason` 由 `length` 转 `stop`）。

    解析用 `salvage_json`：条目是一条条写出来的，截断必然发生在**最后一条中间**，
    救回前面完整的那些比整份丢掉有用（见模块 docstring「不留半成品」那段）。
    """
    if llm is None:
        return []
    prompt = [
        {"role": "system", "content": OUTLINE_PROMPT},
        {"role": "user", "content": _context(node, topic_title=topic_title, report=report)},
    ]
    try:
        raw = await llm.complete(
            prompt,
            response_format={"type": "json_object"},
            max_tokens=OUTLINE_MAX_TOKENS,
            extra_body=THINKING_OFF,
        )
    except Exception:  # noqa: BLE001 — 模型/网络/格式任何一步失败都只是「这次没有提纲」
        return []
    return clean_outline(salvage_json(raw), max_items=max_items)


def clean_outline(parsed, *, max_items: int = MAX_ITEMS) -> list[str]:
    """把模型输出洗成一组干净的条目。**任何形状异常都倒向空**（见模块 docstring）。

    `parsed` 可以是一个 `{"outline": [...]}` 对象，也可以直接是数组——`json_object`
    模式下仍偶有模型直接吐数组，而这两种形状的语义是一样的，没必要为它烧掉一次重试。
    """
    if isinstance(parsed, dict):
        raw = parsed.get("outline")
    elif isinstance(parsed, list):
        raw = parsed
    else:
        raw = None
    if not isinstance(raw, list):
        return []

    out: list[str] = []
    for item in raw:
        # **先筛类型再 `str()`。** `str()` 对任何对象都有返回值，所以 `None` 会变成
        # 字符串 `"None"`、`[]` 会变成 `"[]"`——它们会一路通过后面所有检查，最后作为
        # 一条提纲写进用户自己的文件里。放行数字是因为模型偶尔直接把步骤编号当条目
        # （`[1, 2, 3]`）；`bool` 除外，那是 `int` 的子类，`True` 变成「True」不是提纲。
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            continue
        # `split()` 顺手做两件事：折叠换行/多空格成一行（提纲条目必须占一行，
        # 否则它会被写成一个跨行的列表项），以及去掉首尾空白。
        text = " ".join(str(item).split())
        text = re.sub(r"^(?:\d+[.)]|[-*+·])\s*", "", text).strip()
        if not text:
            continue
        if len(text) > ITEM_LIMIT:
            text = text[:ITEM_LIMIT].rstrip() + "…"
        if text in out:  # 模型偶尔会重复同一条
            continue
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _context(node: dict, *, topic_title: str, report: str) -> str:
    """交给模型的全部上下文。`node` 是 `store.get_node` 的形状。"""
    parts = [
        f"知识点：{node.get('name') or '（未命名）'}",
        f"所属主题：{topic_title or node.get('topic_title') or '（未命名）'}",
        f"类型：{node.get('type') or '（未标注）'}",
        f"一句话说明：{node.get('summary') or '（无）'}",
        f"已有的要点：{'、'.join(node.get('key_points') or []) or '（无）'}",
    ]
    prereqs = [p.get("name") for p in node.get("prerequisites") or [] if p.get("name")]
    parts.append(f"先修知识点：{'、'.join(prereqs) or '无'}")
    excerpt = _excerpt(report, node.get("name") or "")
    if excerpt:
        parts.append(
            "\n主题研究报告的节选（可能只覆盖本知识点的一部分，仅供参考；"
            "与上面矛盾时以上面为准）：\n" + excerpt
        )
    return "\n".join(parts)


def _excerpt(report: str, name: str, *, limit: int = EXCERPT_LIMIT) -> str:
    """从总报告里挑出与这个知识点有关的片段（尽力而为，不是必须成功）。

    优先取**正文里提到该知识点名的段落**：报告是按章节写的，而抽取出的知识点名
    常常被模型改写过（「文本切分」→「分块策略」），所以匹配不上是完全正常的——
    那时退回报告开头，至少给模型一个主题语境，而不是完全空手。绝不因为挑不出
    片段就跳过生成：没有报告节选的提纲依然比没有提纲好。
    """
    text = (report or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    key = (name or "").strip()
    if key:
        hits = [para for para in re.split(r"\n\s*\n", text) if key in para]
        joined = "\n\n".join(hits).strip()
        if joined:
            return joined[:limit]
    return text[:limit]
