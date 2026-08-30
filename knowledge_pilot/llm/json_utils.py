"""LLM 结构化输出的健壮解析。

Planner / Evaluate 等节点要求 LLM 返回 JSON（DeepSeek json_object 模式）。
这里集中处理模型偶尔的「代码围栏包裹 / 前后缀废话 / 缺括号」等脏输出，
解析失败时由调用方回退到安全默认值（如单步计划 / 视为充分）。
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
