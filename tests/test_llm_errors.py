"""错误分类纯函数（Phase 8）：不 import openai，仅靠 status_code / httpx / 类名兜底。

这些判定直接决定网关「重试 / fallback / 直接上抛」的行为，是 Model Gateway 的安全边界：
瞬时错（连接/超时/429/5xx）才允许重试或 fallback；400/401/403/404 等语义错必须原样上抛。
"""

import httpx
import pytest

from knowledge_pilot.llm.errors import (
    ErrorKind,
    backoff_seconds,
    classify_error,
    is_connection_lost,
    is_retryable_status,
    is_transient_error,
)


class _HttpStatusError(Exception):
    """模拟 openai 的 APIStatusError：带 status_code 属性，不 import openai。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class APITimeoutError(Exception):
    """类名兜底：openai.APITimeoutError 不继承 httpx 类，靠名字识别。"""


class APIConnectionError(Exception):
    """类名兜底：openai.APIConnectionError。"""


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (401, ErrorKind.AUTH),
        (403, ErrorKind.AUTH),
        (404, ErrorKind.NOT_FOUND),
        (429, ErrorKind.RATE_LIMIT),
        (400, ErrorKind.INVALID_REQUEST),
        (422, ErrorKind.INVALID_REQUEST),
        (418, ErrorKind.CLIENT),  # 其它 4xx
        (500, ErrorKind.SERVER),
        (503, ErrorKind.SERVER),
        (600, ErrorKind.UNKNOWN),  # 越界
    ],
)
def test_classify_by_status_code(code, kind):
    assert classify_error(_HttpStatusError(code)) is kind


def test_classify_by_transport_exception():
    assert classify_error(httpx.ConnectError("x")) is ErrorKind.CONNECTION
    assert classify_error(httpx.ReadTimeout("x")) is ErrorKind.TIMEOUT


def test_classify_by_builtin_exception():
    # TimeoutError 是 OSError 子类 → 必须先判超时再判连接（否则会被归成 CONNECTION）。
    assert classify_error(TimeoutError("x")) is ErrorKind.TIMEOUT
    assert classify_error(ConnectionError("x")) is ErrorKind.CONNECTION


def test_classify_by_class_name_fallback():
    # 不 import openai 也能识别其异常（类名特征）。
    assert classify_error(APITimeoutError("x")) is ErrorKind.TIMEOUT
    assert classify_error(APIConnectionError("x")) is ErrorKind.CONNECTION


def test_classify_valueerror_is_client_kind():
    assert classify_error(ValueError("bad json_object")) is ErrorKind.CLIENT


def test_classify_unknown():
    assert classify_error(KeyError("nope")) is ErrorKind.UNKNOWN


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_retryable_statuses(code):
    assert is_retryable_status(code) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_non_retryable_statuses(code):
    assert is_retryable_status(code) is False


def test_is_transient_error():
    assert is_transient_error(_HttpStatusError(429)) is True
    assert is_transient_error(_HttpStatusError(500)) is True
    assert is_transient_error(httpx.ConnectError("x")) is True
    assert is_transient_error(TimeoutError("x")) is True
    # 语义错不重试。
    assert is_transient_error(_HttpStatusError(400)) is False
    assert is_transient_error(_HttpStatusError(401)) is False
    assert is_transient_error(ValueError("x")) is False


def test_is_connection_lost():
    assert is_connection_lost(EOFError()) is True
    assert is_connection_lost(BrokenPipeError()) is True
    assert is_connection_lost(ConnectionResetError()) is True
    assert is_connection_lost(httpx.ConnectError("x")) is True
    # server 显式返回的错误不算断线（不应触发 MCP 重连）。
    assert is_connection_lost(_HttpStatusError(500)) is False


def test_backoff_is_linear():
    assert backoff_seconds(1, 1.0) == 1.0
    assert backoff_seconds(2, 1.0) == 2.0
    assert backoff_seconds(3, 0.5) == 1.5


def test_backoff_clamps_non_positive_attempt():
    assert backoff_seconds(0, 2.0) == 2.0
