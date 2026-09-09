"""MCP → OpenAI function schema / CallToolResult→文本 转换（零 mcp 依赖，纯函数）。

不需要安装 mcp：convert 模块只读对象字段（getattr），测试喂普通对象/dict 即可。
"""

from knowledge_pilot.mcp.convert import call_result_to_text, to_openai_function_schema


# ---- to_openai_function_schema -----------------------------------------


def test_schema_maps_basic_tool():
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    out = to_openai_function_schema("search_memory", "查询历史记忆", schema)
    assert out["type"] == "function"
    fn = out["function"]
    assert fn["name"] == "search_memory"
    assert fn["description"] == "查询历史记忆"
    assert fn["parameters"] == schema


def test_schema_missing_description_becomes_empty():
    out = to_openai_function_schema("recent_research", "", {"type": "object", "properties": {}})
    assert out["function"]["description"] == ""


def test_schema_missing_input_schema_gets_empty_object():
    out = to_openai_function_schema("search_papers", "d", None)
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}


def test_schema_non_object_input_schema_gets_empty_object():
    out = to_openai_function_schema("x", "d", "not-a-schema")
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}


def test_schema_missing_properties_gets_empty_object():
    out = to_openai_function_schema("x", "d", {"type": "object"})
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}


def test_schema_no_required_stays_absent():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    out = to_openai_function_schema("x", "d", schema)
    assert "required" not in out["function"]["parameters"]


def test_schema_defaults_type_to_object():
    # MCP server 罕见情况：只有 properties 无 type → 强制 object（防 LLM 拒收）
    out = to_openai_function_schema("x", "d", {"properties": {"a": {"type": "string"}}})
    assert out["function"]["parameters"]["type"] == "object"


# ---- call_result_to_text ------------------------------------------------


def _text_block(text: str):
    return type("C", (), {"text": text})()


def _result(content=None, is_error=False):
    return type("R", (), {"content": content, "isError": is_error})()


def test_call_result_concatenates_text_blocks():
    result = _result(content=[_text_block("a"), _text_block("b")])
    assert call_result_to_text(result) == "a\nb"


def test_call_result_accepts_dict_blocks():
    result = _result(content=[{"text": "hello"}, {"text": "world"}])
    assert call_result_to_text(result) == "hello\nworld"


def test_call_result_prefixes_error():
    result = _result(content=[_text_block("boom")], is_error=True)
    assert call_result_to_text(result) == "（工具执行出错）boom"


def test_call_result_empty_content_fallback():
    result = _result(content=[])
    assert call_result_to_text(result) == "（工具无返回内容）"


def test_call_result_none_content_fallback():
    result = _result(content=None)
    assert call_result_to_text(result) == "（工具无返回内容）"


def test_call_result_snake_is_error_compat():
    # v2 SDK 用 is_error（蛇形）；getattr 双兼容
    result = type("R", (), {"content": [_text_block("x")], "is_error": True})()
    assert call_result_to_text(result) == "（工具执行出错）x"


def test_call_result_ignores_non_text_blocks():
    result = _result(content=[{"type": "image", "data": "..."}, _text_block("ok")])
    assert call_result_to_text(result) == "ok"
