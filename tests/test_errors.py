"""错误码映射测试：重点覆盖 1002 歧义与文档未记载的 2004。"""
from __future__ import annotations

import pytest

from app import errors


@pytest.mark.parametrize(
    ("code", "message", "expected_code", "expected_retryable"),
    [
        # 1002 歧义：未知标的 vs 参数格式错误
        (1002, "Unknown thscode: 999999.SH", "INSTRUMENT_NOT_FOUND", False),
        (1002, "Unknown thscode: 510300.SH", "INSTRUMENT_NOT_FOUND", False),
        (1002, "Invalid parameter format: interval", "INVALID_REQUEST", False),
        # 缺参/越界/冲突
        (1001, "Missing required parameter: q", "INVALID_REQUEST", False),
        (1003, "Parameter value out of range", "INVALID_REQUEST", False),
        (1004, "Conflicting parameters", "INVALID_REQUEST", False),
        # 认证/权限
        (2001, "Unauthenticated", "UNKNOWN", False),
        (2003, "Forbidden", "UNKNOWN", False),
        # 文档未记载的 2004：同花顺AI客户端专用 → 能力缺失，触发源流转
        (2004, "该数据为同花顺AI客户端专用", "UNSUPPORTED_CAPABILITY", False),
        # 标的与能力
        (3001, "Target not found: CU2601.SHF", "INSTRUMENT_NOT_FOUND", False),
        (3004, "Asset type unsupported", "UNSUPPORTED_CAPABILITY", False),
        # 可重试
        (4001, "Rate limited", "UPSTREAM_UNAVAILABLE", True),
        (5001, "Internal error", "UPSTREAM_UNAVAILABLE", True),
        (5002, "Upstream timeout", "TIMEOUT", True),
        (5003, "Data source unavailable", "UPSTREAM_UNAVAILABLE", True),
        # 未知码
        (9999, "Something else", "UNKNOWN", False),
    ],
)
def test_classify(code, message, expected_code, expected_retryable):
    """逐条断言上游码 → KCQ 码与可重试标记。"""
    kcq_code, retryable = errors.classify(code, message)
    assert kcq_code == expected_code
    assert retryable is expected_retryable


def test_only_two_codes_are_routable():
    """只有两个码触发 KCQ 源流转。"""
    assert errors.ROUTABLE_CODES == {"UNSUPPORTED_CAPABILITY", "INSTRUMENT_NOT_FOUND"}


def test_classify_never_returns_code_outside_kcq_set():
    """任何上游码都不得产出 KCQ 枚举之外的值。"""
    for code in [0, 1, 1001, 1002, 1003, 1004, 2001, 2003, 2004, 3001, 3002, 3004,
                 4001, 5001, 5002, 5003, 8888]:
        kcq_code, _ = errors.classify(code, "x")
        assert kcq_code in errors.KCQ_CODES


def test_kcq_codes_matches_frontend_enum():
    """KCQ 枚举必须与前端 provider/types.ts 的 8 个值一致。"""
    assert errors.KCQ_CODES == {
        "INVALID_REQUEST",
        "UNSUPPORTED_CAPABILITY",
        "INSTRUMENT_NOT_FOUND",
        "UPSTREAM_UNAVAILABLE",
        "TIMEOUT",
        "ABORTED",
        "INVALID_RESPONSE",
        "UNKNOWN",
    }


def test_every_kcq_code_has_http_status():
    """每个 KCQ 码都要有 HTTP 映射，且必须是非 2xx。

    KCQ httpTransport 只在 !res.ok 分支解析错误信封（httpTransport.ts:86-99）；
    2xx 会丢失错误码并退化为 FETCH_FAILED，源流转随之失效。
    """
    for code in errors.KCQ_CODES:
        status = errors.http_status_for(code)
        assert 400 <= status <= 599, f"{code} → {status} 不是错误状态码"


def test_routable_codes_map_to_expected_status():
    """两个可流转码映射到 400 / 404（与 MT5/GoTDX 约定一致）。"""
    assert errors.http_status_for("UNSUPPORTED_CAPABILITY") == 400
    assert errors.http_status_for("INSTRUMENT_NOT_FOUND") == 404
    assert errors.http_status_for("UPSTREAM_UNAVAILABLE") == 502
    assert errors.http_status_for("TIMEOUT") == 504


def test_unknown_code_defaults_to_500():
    """未登记的码兜底 500。"""
    assert errors.http_status_for("NOT_A_REAL_CODE") == 500
