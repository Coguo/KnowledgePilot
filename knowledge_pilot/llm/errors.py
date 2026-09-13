"""LLM / MCP 调用错误的纯函数分类层（零 openai import，离线可单测）。

openai SDK 抛的异常不 import 也能分类：
1. 优先读 `getattr(exc, "status_code", None)`（int）→ 按 HTTP 状态码归类；
2. 否则 isinstance 命中 httpx / 内置异常；
3. 最后按类名兜底（openai 的 APITimeoutError / APIConnectionError 等）。

目的：
- Model Gateway（Phase 8）只对「瞬时错误」（连接/超时/429/5xx）退避重试或 fallback，
  400/401/403/404 等语义错误必须原样上抛（不改变调用方既有解析失败回退语义）。
- MCP stdio 断线判定（is_connection_lost）复用它，避免两个子项各自造轮子。
"""

from __future__ import annotations

import enum

import httpx


class ErrorKind(enum.Enum):
    CONNECTION = "connection"  # 连接失败（httpx TransportError / 内置 ConnectionError）
    TIMEOUT = "timeout"  # 读/写超时（httpx TimeoutException / 内置 TimeoutError）
    RATE_LIMIT = "rate_limit"  # 429
    SERVER = "server"  # 5xx
    INVALID_REQUEST = "invalid_request"  # 400 / 422 等「请求本身错」（如 json_object 缺词）
    AUTH = "auth"  # 401 / 403
    NOT_FOUND = "not_found"  # 404
    CLIENT = "client"  # 其它 4xx
    UNKNOWN = "unknown"


# 瞬时、值得重试的状态码（网关层退避重试的判定集合）。
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def classify_error(exc: BaseException) -> ErrorKind:
    """把异常归为一种 ErrorKind。决策不依赖具体 SDK 异常类型，A 轨可测。"""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in (401, 403):
            return ErrorKind.AUTH
        if status == 404:
            return ErrorKind.NOT_FOUND
        if status == 429:
            return ErrorKind.RATE_LIMIT
        if status in (400, 422):
            return ErrorKind.INVALID_REQUEST
        if 400 <= status < 500:
            return ErrorKind.CLIENT
        if 500 <= status < 600:
            return ErrorKind.SERVER
        return ErrorKind.UNKNOWN

    # httpx 传输层（httpx.TimeoutException 是 TransportError 子类 → 先判超时）。
    if isinstance(exc, httpx.TimeoutException):
        return ErrorKind.TIMEOUT
    if isinstance(exc, httpx.TransportError):
        return ErrorKind.CONNECTION
    # 内置超时 / 连接类。
    if isinstance(exc, TimeoutError):
        return ErrorKind.TIMEOUT
    if isinstance(exc, (ConnectionError, OSError)):
        return ErrorKind.CONNECTION
    # openai 异常兜底：APIConnectionError / APITimeoutError 不继承 httpx 对应类，
    # 但其类名带明确特征（不 import openai 也能识别）。
    name = type(exc).__name__
    if "APITimeout" in name or name.endswith("Timeout"):
        return ErrorKind.TIMEOUT
    if "APIConnection" in name:
        return ErrorKind.CONNECTION
    if isinstance(exc, ValueError):
        return ErrorKind.CLIENT
    return ErrorKind.UNKNOWN


def is_retryable_status(status_code: int) -> bool:
    """状态码是否值得网关重试（408/429/5xx）。"""
    return status_code in _RETRYABLE_STATUSES


def is_transient_error(exc: BaseException) -> bool:
    """是否是「瞬时、可重试/可 fallback」的错误（连接/超时/429/5xx）。"""
    return classify_error(exc) in (
        ErrorKind.CONNECTION,
        ErrorKind.TIMEOUT,
        ErrorKind.RATE_LIMIT,
        ErrorKind.SERVER,
    )


def is_connection_lost(exc: BaseException) -> bool:
    """MCP stdio 连接是否已丢（子进程退出/管道断），需要重连的判定。

    保守起见只把「传输层断/连接失败」算断线；server 显式返回的调用错误
    （如 McpError，带服务端错误码）不算，不应触发重连。
    """
    if isinstance(exc, (EOFError, BrokenPipeError, ConnectionResetError)):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    kind = classify_error(exc)
    return kind in (ErrorKind.CONNECTION, ErrorKind.TIMEOUT)


def backoff_seconds(attempt: int, base_seconds: float) -> float:
    """第 attempt 次重试（1 起）的退避秒数：线性递增 base * attempt。

    尝试用线性而非指数：个人/本地服务场景瞬时抖动短，线性退避已够、延迟更可预期。
    """
    return base_seconds * max(attempt, 1)
