"""MCP 结果的纯函数转换层：零 mcp 依赖，离线可单测。

需要把两种「MCP 原生形状」翻译成 Agent 能吃的形状：
1. 工具声明（mcp 的 Tool.inputSchema，JSON Schema）→ OpenAI function schema
   （DeepSeek 兼容；LLM 只吃 {"type":"function","function":{...}}）。
2. 工具调用结果（mcp 的 CallToolResult）→ 给 LLM/UI 的自由文本。

保持纯函数：输入对象用 getattr 鸭子类型读取（不 import mcp），
因此本模块在未安装 mcp 的环境也能安全导入与测试。
"""

__all__ = ["to_openai_function_schema", "call_result_to_text"]

# 参数骨架：MCP 工具 inputSchema 缺失/非 object 时兜底的空参数
# （防 LLM 端拒收空/畸形 parameters；search_memory 这类参数仍会带真实 schema）。
_EMPTY_PARAMETERS = {"type": "object", "properties": {}}


def to_openai_function_schema(name: str, description: str, input_schema: object) -> dict:
    """把 MCP Tool 的 (name, description, inputSchema) 转成 OpenAI function schema。

    input_schema 正常是 {"type":"object","properties":{...},"required":[...]}；
    缺失/非 dict/无 properties 时归一化为空骨架，避免模型收到畸形 parameters。
    """
    parameters = _normalize_parameters(input_schema)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or "",
            "parameters": parameters,
        },
    }


def _normalize_parameters(input_schema: object) -> dict:
    """把 MCP 工具参数 schema 归一化为 OpenAI 可用的 parameters。"""
    if not isinstance(input_schema, dict):
        return _EMPTY_PARAMETERS
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return _EMPTY_PARAMETERS
    params: dict = {
        "type": input_schema.get("type") if isinstance(input_schema.get("type"), str) else "object",
        "properties": properties,
    }
    required = input_schema.get("required")
    if isinstance(required, list) and required:
        params["required"] = required
    return params


def call_result_to_text(result: object) -> str:
    """把 MCP 的 CallToolResult 压成一段文本，给 LLM 作 tool message 回填。

    兼容 v1（.isError / TextContent）与 v2（.is_error）的字段命名差异；
    content 支持 TextContent 对象 / dict / 字符串。出错或无内容时给可读兜底。
    """
    is_error = getattr(
        result, "isError", getattr(result, "is_error", False)
    )  # v1 camelCase → v2 snake_case 双兼容
    content = getattr(result, "content", None)
    text = "\n".join(_content_texts(content))

    if not text.strip():
        text = "（工具无返回内容）"
    if is_error:
        text = f"（工具执行出错）{text}"
    return text


def _content_texts(content: object) -> list[str]:
    """从 CallToolResult.content（列表）抽取文本块，容忍对象/dict/字符串。"""
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for block in content:
        if isinstance(block, dict):
            t = block.get("text")
        else:
            t = getattr(block, "text", None)
        if isinstance(t, str) and t:
            out.append(t)
    return out
