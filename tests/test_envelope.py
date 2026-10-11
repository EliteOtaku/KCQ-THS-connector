"""信封测试：KCQ camelCase 结构与上游解析。"""
from __future__ import annotations

import re

import pytest

from app.envelope import EnvelopeParseError, fail, ok, parse_upstream


def test_ok_envelope_shape():
    """成功信封恰为 {data, requestId}。"""
    envelope = ok({"a": 1})
    assert set(envelope.keys()) == {"data", "requestId"}
    assert envelope["data"] == {"a": 1}
    assert re.fullmatch(r"[0-9a-f]{32}", envelope["requestId"])


def test_fail_envelope_shape():
    """错误信封恰为 {error:{code,message}, requestId}。"""
    envelope = fail("INVALID_REQUEST", "bad")
    assert set(envelope.keys()) == {"error", "requestId"}
    assert envelope["error"] == {"code": "INVALID_REQUEST", "message": "bad"}


def test_fail_with_details():
    """details 存在时才带该字段。"""
    envelope = fail("UNKNOWN", "x", {"k": "v"})
    assert envelope["error"]["details"] == {"k": "v"}


def test_request_ids_are_unique():
    """每次调用生成不同 requestId。"""
    assert ok({})["requestId"] != ok({})["requestId"]


def test_parse_upstream_ok():
    """正常信封解析。"""
    envelope = parse_upstream(
        {"code": 0, "message": "success", "request_id": "r1", "data": {"item": []}}
    )
    assert envelope.code == 0
    assert envelope.data == {"item": []}
    assert envelope.request_id == "r1"


def test_parse_upstream_tolerates_missing_optional_fields():
    """message/request_id/data 缺失时按空值兜底。"""
    envelope = parse_upstream({"code": 0})
    assert envelope.message == ""
    assert envelope.request_id == ""
    assert envelope.data is None


@pytest.mark.parametrize("raw", [None, [], "text", 42, {"message": "no code"}])
def test_parse_upstream_rejects_invalid(raw):
    """结构非法抛 EnvelopeParseError（→ INVALID_RESPONSE）。"""
    with pytest.raises(EnvelopeParseError):
        parse_upstream(raw)
