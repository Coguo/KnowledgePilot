"""LLM 结构化输出的健壮解析。

Planner / Evaluate 等节点要求 LLM 返回 JSON（DeepSeek json_object 模式）。
这里集中处理模型偶尔的「代码围栏包裹 / 前后缀废话 / 缺括号」等脏输出，
解析失败时由调用方回退到安全默认值（如单步计划 / 视为充分）。

`salvage_json`（第七轮）额外处理**被截断的输出**——那是推理模型的典型故障：
输出预算被推理吃掉时正文停在半句，JSON 永远等不到收尾括号。
"""

import json
import re


def parse_json_object(text: str):
    """从 LLM 文本中提取并解析 JSON 对象/数组；失败返回 None。

    依次尝试：
    1. 直接 json.loads（干净输出）；
    2. 剥掉 ```json ... ``` 代码围栏后 json.loads；
    3. 用正则定位首个平衡的 {…} 或 […] 块（容忍前后缀废话）。
    """
    text = (text or "").strip()
    if not text:
        return None

    # 1) 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) 剥代码围栏
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    # 3) 正则取首个平衡的 {…} / […]（含嵌套括号）
    for start, end in (("{", "}"), ("[", "]")):
        begin = text.find(start)
        if begin == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(begin, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == start:
                depth += 1
            elif ch == end:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[begin : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


_CLOSER = {"{": "}", "[": "]"}
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", flags=re.DOTALL)


def _checkpoints(body: str) -> list[tuple[int, tuple[str, ...]]]:
    """列出 body 里每个**取值边界**：(结束下标, 闭合后仍未闭合的括号栈)。

    两类边界，都要求「补上收尾括号后就是合法 JSON」：

    - 括号闭合处（`}` / `]`）；
    - **数组里的字符串闭合处**——`{"outline": ["甲", "乙", "丙` 这种形状截断在最后一个
      条目中间时，一个括号都没闭合，只认括号的话这一段就整个救不回来。而它恰恰是
      「数组装字符串」的必然截断形态（提纲就是这个形状）。

    字符串内部的括号不计——`"关键词：BM25（稀疏）"` 里的中文括号不参与，`"\\""` 这类转义
    也照 `parse_json_object` 的同一套规则跳过。栈一旦不配对（多了个 `}`）就停：再往后
    扫出来的位置都不可信。
    """
    stack: list[str] = []
    out: list[tuple[int, tuple[str, ...]]] = []
    in_str = False
    escape = False
    for i, ch in enumerate(body):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
                if stack and stack[-1] == "[":
                    out.append((i + 1, tuple(stack)))
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            out.append((i + 1, tuple(stack)))
    return out


def _salvage_from(body: str):
    """在 body 的各个括号闭合点上补足收尾括号，取**最长的那个能解析的**前缀。

    「最长」= 最靠后的闭合点 = 救回最多内容。补出来的是**合法 JSON**，但截断处所在的
    那个元素可能只剩「尾部字段缺失」的形态——实测常见的是少了最后那个 `order`，而
    `order` 缺席是下游早就能处理的（`path.py::_as_int(None)` → 0）。**每一个值都是完整的**，
    不会出现半句话，这一点与 `parse_json_object` 的「取首个平衡块」是一致的取舍。
    """
    for end, open_stack in reversed(_checkpoints(body)):
        suffix = "".join(_CLOSER[b] for b in reversed(open_stack))
        try:
            got = json.loads(body[:end] + suffix)
        except json.JSONDecodeError:
            continue
        if isinstance(got, (dict, list)):
            return got
    return None


def salvage_json(text: str):
    """`parse_json_object`，但在输出**被截断**时交回已经完整的那部分。

    优先级：**完整对象 > 修补出的对象 > 修补出的数组 > `parse_json_object` 拿到的任何东西**。

    早退那一档为什么不能只看「是不是 dict」——`parse_json_object` 在这种残缺输入上会捡回
    **一部分**当结果，两种形状各踩一个坑（两处都已逐字实测，不是推演）：

    - **外层对象截断在「数组闭合之后、`}` 之前」** → 它捡回那个**内层数组**
      （`{"nodes": [{…}, {…}]` 少一个 `}` → 返回 `[{…}, {…}]`），既不是 `None`
      也不是 `{"nodes": …}`；
    - **裸数组被截断** → 它捡回**第一个元素**（`[{…}, {"type"` → 返回 `{…}`），
      于是交上去的是一个只有 1 个节点的 dict。

    （注意第一种要的是「数组真的闭合了」。若最后一个元素本身断在中间
    ——`{"nodes": [{…}, {"name": "B", "key_points": ["y`——那 `[` 与 `{` 两条路径都
    配不平，它返回的是 `None`。两种都会遇到，所以下面的候选既要能补数组、也要能补对象。）

    两种情况下它都「不是 None」，所以「parsed 不是 None 就早退」会把最需要修补的输入
    整条跳过。实测第一次踩的就是它：那次 12 个知识点的回复里最后一个断在
    `…"可用LLM judge对难负例标注"]`、少了收尾的 `}`，于是一份好答案变成了
    **零个节点**（下游静默降级成研究计划）。救回完整的那些，比一个都不留有用得多。

    为什么需要它：推理模型的输出预算被推理吃满时正文会停在半句（`finish_reason=length`、
    一个异常都不抛）。注意它只能救「有正文但被截断」这一种——若推理把额度吃到正文
    **一个字都没有**（同样实测到过），这里没有东西可救，那种只能靠调用方关掉思考
    （`providers.py::THINKING_OFF`）。
    """
    raw = (text or "").strip()
    parsed = parse_json_object(text)
    # 以 `[` 开头时，dict 解析结果只可能是数组里的**一个元素**，不能当整份结果。
    if isinstance(parsed, dict) and raw[:1] != "[":
        return parsed  # 正常路径:完整对象一个字节都不动

    if raw:
        candidates = [raw]
        fence = _FENCE.search(raw)
        if fence:
            candidates.append(fence.group(1).strip())
        # 前缀废话（「好的，结果如下：」）：从**文本里最早的**那个括号起算。
        #
        # 判据是「整段压根不以括号开头」而不是「括号不在第 0 位」：后者会把一份裸数组
        # `[{…}, {…}]` 从第 1 位的 `{` 起切，于是**第一个元素**冒充了整份结果。同理也不能
        # 把 `{` / `[` 两个都当候选试一遍。
        if raw[:1] not in ("{", "["):
            begins = [i for i in (raw.find("{"), raw.find("[")) if i >= 0]
            if begins:
                candidates.append(raw[min(begins):])

        restored = None
        for body in candidates:
            if not body:
                continue
            got = _salvage_from(body)
            if got is None:
                continue
            if isinstance(got, dict):
                return got  # 修补出的**对象**压过内层数组/元素/None
            if restored is None:
                restored = got
        if restored is not None and (parsed is None or raw[:1] == "["):
            return restored

    return parsed
