"""错误码层：上游业务码 → KCQ MarketDataErrorCode 的映射。

上游业务错误 HTTP 状态码恒为 200，错误经响应信封的 code 字段表达；
本模块是唯一的翻译点。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 上游业务码（实测所得，含官方文档未记载的 2004）
# ---------------------------------------------------------------------------
CODE_OK = 0
CODE_MISSING_PARAM = 1001
CODE_BAD_PARAM = 1002  # 歧义：也可能是"未知标的"，见 classify
CODE_OUT_OF_RANGE = 1003
CODE_PARAM_CONFLICT = 1004
CODE_UNAUTHENTICATED = 2001
CODE_FORBIDDEN = 2003
CODE_CLIENT_ONLY = 2004  # 同花顺AI客户端专用（官方文档未记载）
CODE_TARGET_NOT_FOUND = 3001
CODE_DATA_NOT_READY = 3002
CODE_ASSET_TYPE_UNSUPPORTED = 3004
CODE_RATE_LIMITED = 4001
CODE_INTERNAL = 5001
CODE_UPSTREAM_TIMEOUT = 5002
CODE_UPSTREAM_UNAVAILABLE = 5003

# ---------------------------------------------------------------------------
# KCQ MarketDataErrorCode 全集（packages/core/src/data/provider/types.ts）
# 只有这 8 个值，不得发明新值。
# ---------------------------------------------------------------------------
INVALID_REQUEST = "INVALID_REQUEST"
UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
TIMEOUT = "TIMEOUT"
ABORTED = "ABORTED"
INVALID_RESPONSE = "INVALID_RESPONSE"
UNKNOWN = "UNKNOWN"

KCQ_CODES = frozenset(
    {
        INVALID_REQUEST,
        UNSUPPORTED_CAPABILITY,
        INSTRUMENT_NOT_FOUND,
        UPSTREAM_UNAVAILABLE,
        TIMEOUT,
        ABORTED,
        INVALID_RESPONSE,
        UNKNOWN,
    }
)

# 仅这两个触发 KCQ 源流转（SOURCE_REJECTION_CODES）。
ROUTABLE_CODES = frozenset({UNSUPPORTED_CAPABILITY, INSTRUMENT_NOT_FOUND})

# KCQ 错误码 → HTTP 状态码。
#
# 必须是非 2xx：KCQ 的 httpTransport 只在 `!res.ok` 分支解析错误信封
# （protocol/impl/httpTransport.ts:86-99）；2xx 时它要求 body 含 data，否则抛
# 通用 FETCH_FAILED 并**丢失错误码**，导致 UNSUPPORTED_CAPABILITY /
# INSTRUMENT_NOT_FOUND 无法触发源流转。与 MT5 connector、GoTDX 的约定一致。
HTTP_STATUS_BY_CODE: dict[str, int] = {
    INVALID_REQUEST: 400,
    UNSUPPORTED_CAPABILITY: 400,
    INSTRUMENT_NOT_FOUND: 404,
    UPSTREAM_UNAVAILABLE: 502,
    TIMEOUT: 504,
    INVALID_RESPONSE: 502,
    ABORTED: 499,
    UNKNOWN: 500,
}


def http_status_for(code: str) -> int:
    """KCQ 错误码 → HTTP 状态码；未知码按 500。"""
    return HTTP_STATUS_BY_CODE.get(code, 500)


# 上游"未知标的"在 1002 中的判别标记（实测 message 形如 "Unknown thscode: 999999.SH"）
_UNKNOWN_THSCODE_MARKER = "Unknown thscode"


def classify(code: int, message: str) -> tuple[str, bool]:
    """上游业务码 → (KCQ 错误码, 是否可重试)。

    1002 同时表示"参数格式错误"与"未知标的"，必须读 message 消歧：
    误判为 INVALID_REQUEST 会让 KCQ 不流转到下一个源（用户看到硬失败）；
    误判为 INSTRUMENT_NOT_FOUND 会静默流转并掩盖 bug。
    """
    if code == CODE_BAD_PARAM:
        if _UNKNOWN_THSCODE_MARKER in message:
            return INSTRUMENT_NOT_FOUND, False
        return INVALID_REQUEST, False
    if code in (CODE_MISSING_PARAM, CODE_OUT_OF_RANGE, CODE_PARAM_CONFLICT):
        return INVALID_REQUEST, False
    if code in (CODE_UNAUTHENTICATED, CODE_FORBIDDEN):
        return UNKNOWN, False
    if code == CODE_CLIENT_ONLY:
        # 同花顺AI客户端专用：对本连接器而言是能力缺失，应触发源流转
        return UNSUPPORTED_CAPABILITY, False
    if code == CODE_TARGET_NOT_FOUND:
        return INSTRUMENT_NOT_FOUND, False
    if code == CODE_ASSET_TYPE_UNSUPPORTED:
        return UNSUPPORTED_CAPABILITY, False
    if code == CODE_RATE_LIMITED:
        return UPSTREAM_UNAVAILABLE, True
    if code == CODE_INTERNAL:
        return UPSTREAM_UNAVAILABLE, True
    if code == CODE_UPSTREAM_TIMEOUT:
        return TIMEOUT, True
    if code == CODE_UPSTREAM_UNAVAILABLE:
        return UPSTREAM_UNAVAILABLE, True
    return UNKNOWN, False
