"""信封层：KCQ 协议信封的封装与上游信封的解析。

KCQ 侧字段为 camelCase（ProtocolEnvelope / ProtocolErrorEnvelope），
上游侧为 snake_case（ApiResponse）——本模块是两者唯一的转换点。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from .errors import INVALID_RESPONSE


def new_request_id() -> str:
    """生成 32 位十六进制 requestId（KCQ 侧追踪 ID）。"""
    return uuid.uuid4().hex


def ok(data: object) -> dict:
    """KCQ 成功信封：{"data": …, "requestId": …}。"""
    return {"data": data, "requestId": new_request_id()}


def fail(code: str, message: str, details: dict | None = None) -> dict:
    """KCQ 错误信封：{"error": {"code","message","details"?}, "requestId": …}。

    code 必须是 KCQ MarketDataErrorCode 之一。
    """
    error: dict = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error, "requestId": new_request_id()}


@dataclass(frozen=True)
class UpstreamEnvelope:
    """上游 ApiResponse 的解析结果。"""

    code: int
    message: str
    data: dict | None
    request_id: str


class EnvelopeParseError(Exception):
    """上游响应不符合信封契约。"""

    kcq_code = INVALID_RESPONSE

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_upstream(raw: object) -> UpstreamEnvelope:
    """解析上游响应体；结构非法抛 EnvelopeParseError（→ INVALID_RESPONSE）。

    合法前提：JSON 对象且 code 为整数。message/request_id/data 缺失时按空值兜底。
    """
    if not isinstance(raw, dict):
        raise EnvelopeParseError("上游响应不是 JSON 对象")
    code = raw.get("code")
    if not isinstance(code, int):
        raise EnvelopeParseError("上游响应缺少整数 code 字段")
    message = raw.get("message")
    request_id = raw.get("request_id")
    data = raw.get("data")
    return UpstreamEnvelope(
        code=code,
        message=message if isinstance(message, str) else "",
        data=data if isinstance(data, dict) else None,
        request_id=request_id if isinstance(request_id, str) else "",
    )
