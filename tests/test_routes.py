"""路由与信封集成测试：走 TestClient，全程无网络。"""
from __future__ import annotations

import pytest

from app import bars, catalog, calendar as calendar_mod

A_SHARE_ITEM = {
    "date_ms": 1_797_000_000_000,
    "open_price": 10.0,
    "high_price": 11.0,
    "low_price": 9.5,
    "close_price": 10.5,
    "volume": 1000,
    "turnover": 10500.0,
}

BARS_BODY = {
    "sourceId": "ths",
    "instrument": {
        "id": "ths:600519.SH",
        "symbol": "600519.SH",
        "exchange": "SH",
        "providerRef": {"thscode": "600519.SH", "assetType": "a-share"},
    },
    "period": "daily",
    "adjustment": "none",
    "barAggregation": "original",
    "limit": 30,
}


def test_health_live(client):
    """存活探针。"""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "alive"


def test_probe_declares_daily_only(client, fake_upstream):
    """probe 只声明 daily，且不声明未实现能力。"""
    fake_upstream.set_response(calendar_mod.PATH_TRADING_DAYS, {"item": []})
    response = client.get("/api/v1/market-data/sources/ths/probe")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "online"
    assert data["capabilities"]["bars"]["periods"] == ["daily"]
    for absent in ["liveBars", "timeShare", "timeShareRange", "depth", "marketTicks"]:
        assert absent not in data["capabilities"]


def test_probe_unknown_source_404(client):
    """未知 sourceId → 404。"""
    assert client.get("/api/v1/market-data/sources/other/probe").status_code == 404


def test_probe_degraded_without_key(app, fake_upstream, settings):
    """未配置 key 时 probe 返回 degraded，而不是崩溃。"""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    no_key = replace(settings, api_key=None)
    application = create_app(no_key, upstream=fake_upstream)
    with TestClient(application) as test_client:
        response = test_client.get("/api/v1/market-data/sources/ths/probe")
    assert response.json()["data"]["status"] == "degraded"


def test_bars_success_envelope(client, fake_upstream):
    """bars 成功信封恰为 {data, requestId}。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    response = client.post("/api/v1/market-data/bars", json=BARS_BODY)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"data", "requestId"}
    data = body["data"]
    assert data["volumeUnit"] == "share"
    assert data["timezone"] == "Asia/Shanghai"
    assert len(data["items"]) == 1


def test_bars_unsupported_period_returns_error_envelope(client, fake_upstream):
    """请求 1min → 400 + UNSUPPORTED_CAPABILITY（触发 KCQ 源流转）。"""
    body = {**BARS_BODY, "period": "1min"}
    response = client.post("/api/v1/market-data/bars", json=body)
    # 必须非 2xx：KCQ httpTransport 只在 !res.ok 分支解析错误信封
    assert response.status_code == 400
    payload = response.json()
    assert set(payload.keys()) == {"error", "requestId"}
    assert payload["error"]["code"] == "UNSUPPORTED_CAPABILITY"


def test_bars_upstream_error_maps_to_envelope(client, fake_upstream):
    """上游 2004（客户端专用）→ UNSUPPORTED_CAPABILITY + 400。"""
    from app.envelope import UpstreamEnvelope

    fake_upstream.set_raw(
        "/api/a-share/prices/historical",
        UpstreamEnvelope(code=2004, message="该数据为同花顺AI客户端专用", data=None,
                         request_id="r"),
    )
    response = client.post("/api/v1/market-data/bars", json=BARS_BODY)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "UNSUPPORTED_CAPABILITY"


def test_search_endpoint(client, fake_upstream):
    """检索端点。"""
    fake_upstream.set_response(
        catalog.PATH_SEARCH,
        {"item": [{"thscode": "600519.SH", "name": "贵州茅台", "exchange": "SH",
                   "asset_type": "a-share", "currency": "CNY"}]},
    )
    response = client.post(
        "/api/v1/market-data/instruments/search",
        json={"sourceId": "ths", "keyword": "茅台", "limit": 10},
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["id"] == "ths:600519.SH"


def test_trading_calendar_endpoint(client, fake_upstream):
    """交易日历返回 anchor 之后的槽位。"""
    fake_upstream.set_response(
        calendar_mod.PATH_TRADING_DAYS,
        {"item": [{"date_ms": 1000, "date": "19700101"},
                  {"date_ms": 2000, "date": "19700101"},
                  {"date_ms": 3000, "date": "19700101"}]},
    )
    response = client.post(
        "/api/v1/market-data/trading-calendar",
        json={"sourceId": "ths", "anchorTimestamp": 1500, "count": 5},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["anchorTimestamp"] == 1500
    assert data["futureTimestamps"] == [2000, 3000]


def test_timeshare_unsupported(client):
    """分时端点 P0 未实现 → 400 + UNSUPPORTED_CAPABILITY。"""
    response = client.post("/api/v1/market-data/timeshare", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNSUPPORTED_CAPABILITY"


def test_cors_header_present(client, fake_upstream):
    """CORS 放开，供 5175 直连。"""
    response = client.get("/health/live", headers={"Origin": "http://127.0.0.1:5175"})
    assert response.headers.get("access-control-allow-origin") == "*"


def test_bars_instrument_not_found_passes_through(client, fake_upstream):
    """上游 1002 Unknown thscode → 404 + INSTRUMENT_NOT_FOUND（可流转）。"""
    from app.envelope import UpstreamEnvelope

    fake_upstream.set_raw(
        "/api/a-share/prices/historical",
        UpstreamEnvelope(code=1002, message="Unknown thscode: 600519.SH", data=None,
                         request_id="r"),
    )
    response = client.post("/api/v1/market-data/bars", json=BARS_BODY)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INSTRUMENT_NOT_FOUND"


def test_upstream_unavailable_is_502(client, fake_upstream):
    """上游不可用 → 502 + UPSTREAM_UNAVAILABLE（不触发源流转）。"""
    from app.envelope import UpstreamEnvelope

    fake_upstream.set_raw(
        "/api/a-share/prices/historical",
        UpstreamEnvelope(code=5003, message="数据源不可用", data=None, request_id="r"),
    )
    response = client.post("/api/v1/market-data/bars", json=BARS_BODY)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
