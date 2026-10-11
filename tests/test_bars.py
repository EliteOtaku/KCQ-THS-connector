"""K 线层测试：域路由、时间字段、量纲、复权、周期拒绝。"""
from __future__ import annotations

import pytest

from app import bars
from app.upstream import ThsApiError

NOW = 1_800_000_000_000


def instrument(thscode: str, asset_type: str) -> dict:
    """构造 KCQ instrument 引用。"""
    return {
        "id": f"ths:{thscode}",
        "symbol": thscode,
        "exchange": "SH",
        "providerRef": {"thscode": thscode, "assetType": asset_type},
    }


A_SHARE_ITEM = {
    "date_ms": 1_797_000_000_000,
    "open_price": 10.0,
    "high_price": 11.0,
    "low_price": 9.5,
    "close_price": 10.5,
    "volume": 1000,
    "turnover": 10500.0,
}
FUTURES_ITEM = {
    "timestamp": 1_797_000_000_000,
    "open_price": 7000.0,
    "high_price": 7100.0,
    "low_price": 6900.0,
    "close_price": 7050.0,
    "volume": 100,
    "turnover": 141_000_000.0,
}


@pytest.mark.parametrize(
    ("asset_type", "thscode", "expected_path"),
    [
        ("a-share", "600519.SH", "/api/a-share/prices/historical"),
        ("a-share-index", "000001.SH", "/api/a-share-index/prices/historical"),
        ("fund-etf", "510300.SH", "/api/fund/market/historical"),
        ("futures", "IC2612.CFE", "/api/futures/prices/daily"),
        ("options", "10011425.SH", "/api/options/prices/daily"),
    ],
)
async def test_routes_to_correct_endpoint(
    fake_upstream, asset_type, thscode, expected_path
):
    """五种资产类型各自路由到正确端点（域不可混用）。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    await bars.fetch_bars(
        fake_upstream,
        instrument=instrument(thscode, asset_type),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    assert fake_upstream.calls[0][0] == expected_path


@pytest.mark.parametrize("asset_type", ["a-share", "a-share-index", "fund-etf"])
async def test_date_ms_field_parsed(fake_upstream, asset_type):
    """A股/指数/ETF 用 date_ms 作为时间字段。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    result = await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("600519.SH", asset_type),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    assert result["items"][0]["timestamp"] == 1_797_000_000_000


@pytest.mark.parametrize("asset_type", ["futures", "options"])
async def test_timestamp_field_parsed(fake_upstream, asset_type):
    """期货/期权用 timestamp 作为时间字段。"""
    fake_upstream.set_default({"item": [FUTURES_ITEM]})
    result = await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("IC2612.CFE", asset_type),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    assert result["items"][0]["timestamp"] == 1_797_000_000_000


@pytest.mark.parametrize(
    ("asset_type", "expected_unit"),
    [
        ("a-share", "share"),
        ("a-share-index", "share"),
        ("fund-etf", "share"),
        ("futures", "contract"),
        ("options", "contract"),
    ],
)
async def test_volume_unit_is_explicit(fake_upstream, asset_type, expected_unit):
    """量纲必须显式返回：A股/指数/ETF=股，期货/期权=手。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    result = await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("600519.SH", asset_type),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    assert result["volumeUnit"] == expected_unit


@pytest.mark.parametrize(
    ("adjustment", "expected_param"),
    [("none", "none"), ("qfq", "forward"), ("hfq", "backward")],
)
async def test_adjust_mapping_for_a_share(fake_upstream, adjustment, expected_param):
    """A 股复权映射：none→none / qfq→forward / hfq→backward。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("600519.SH", "a-share"),
        period="daily",
        adjustment=adjustment,
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    params = fake_upstream.calls_for("/api/a-share/prices/historical")[0]
    assert params["adjust"] == expected_param


@pytest.mark.parametrize(
    "asset_type", ["a-share-index", "fund-etf", "futures", "options"]
)
async def test_adjust_not_sent_for_non_a_share(fake_upstream, asset_type):
    """非 A 股不传 adjust 参数。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("000001.SH", asset_type),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    _, params = fake_upstream.calls[0]
    assert "adjust" not in params


async def test_splits_rejected(fake_upstream):
    """splits 复权不支持。"""
    with pytest.raises(ThsApiError) as exc:
        await bars.fetch_bars(
            fake_upstream,
            instrument=instrument("600519.SH", "a-share"),
            period="daily",
            adjustment="splits",
            bar_aggregation="original",
            limit=10,
            before_timestamp=None,
            now_ms=NOW,
        )
    assert exc.value.kcq_code == "UNSUPPORTED_CAPABILITY"


@pytest.mark.parametrize("asset_type", ["a-share-index", "fund-etf", "futures", "options"])
async def test_adjust_rejected_for_non_a_share(fake_upstream, asset_type):
    """非 A 股请求复权 → 拒绝，而非忽略参数返回未复权数据。"""
    with pytest.raises(ThsApiError) as exc:
        await bars.fetch_bars(
            fake_upstream,
            instrument=instrument("000001.SH", asset_type),
            period="daily",
            adjustment="qfq",
            bar_aggregation="original",
            limit=10,
            before_timestamp=None,
            now_ms=NOW,
        )
    assert exc.value.kcq_code == "UNSUPPORTED_CAPABILITY"


@pytest.mark.parametrize("period", ["1min", "5min", "60min", "weekly", "monthly", "4h"])
async def test_non_daily_period_rejected(fake_upstream, period):
    """只支持日线；其他周期一律 UNSUPPORTED_CAPABILITY（触发源流转）。"""
    with pytest.raises(ThsApiError) as exc:
        await bars.fetch_bars(
            fake_upstream,
            instrument=instrument("600519.SH", "a-share"),
            period=period,
            adjustment="none",
            bar_aggregation="original",
            limit=10,
            before_timestamp=None,
            now_ms=NOW,
        )
    assert exc.value.kcq_code == "UNSUPPORTED_CAPABILITY"


async def test_start_and_end_always_sent(fake_upstream):
    """必须显式传 start/end（期货/期权省略会返回上游隐式窗口）。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("IC2612.CFE", "futures"),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=30,
        before_timestamp=None,
        now_ms=NOW,
    )
    params = fake_upstream.calls_for("/api/futures/prices/daily")[0]
    assert "start" in params and "end" in params


@pytest.mark.parametrize(
    ("asset_type", "expected_param", "expected_value"),
    [
        ("a-share", "interval", "1d"),
        ("a-share-index", "interval", "1d"),
        ("fund-etf", "interval", "1d"),
        ("futures", "time_period", "day_1"),
        ("options", "time_period", "day_1"),
    ],
)
async def test_period_parameter_per_domain(
    fake_upstream, asset_type, expected_param, expected_value
):
    """周期参数名因域而异且必须显式传（A股/指数缺失会返回 1001）。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("600519.SH", asset_type),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    _, params = fake_upstream.calls[0]
    assert params[expected_param] == expected_value
    # 不得同时出现另一个域的参数名
    other = "time_period" if expected_param == "interval" else "interval"
    assert other not in params


async def test_limit_truncates_to_last_items(fake_upstream):
    """只取窗口内最后 limit 根。"""
    items = [{**A_SHARE_ITEM, "date_ms": 1_797_000_000_000 + i * 86_400_000} for i in range(50)]
    fake_upstream.set_default({"item": items})
    result = await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("600519.SH", "a-share"),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    assert len(result["items"]) == 10
    assert result["items"][-1]["timestamp"] == 1_797_000_000_000 + 49 * 86_400_000


async def test_response_echoes_request_fields(fake_upstream):
    """响应原样回传 instrumentId / period / adjustment / barAggregation。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    result = await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("600519.SH", "a-share"),
        period="daily",
        adjustment="qfq",
        bar_aggregation="aligned",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    assert result["instrumentId"] == "ths:600519.SH"
    assert result["period"] == "daily"
    assert result["adjustment"] == "qfq"
    assert result["barAggregation"] == "aligned"
    assert result["timezone"] == "Asia/Shanghai"


async def test_bar_aggregation_all_accepted(fake_upstream):
    """三种聚合口径均接受（同花顺日线本身即上海零点边界）。"""
    fake_upstream.set_default({"item": [A_SHARE_ITEM]})
    for aggregation in ["original", "aligned", "europe-traditional"]:
        result = await bars.fetch_bars(
            fake_upstream,
            instrument=instrument("600519.SH", "a-share"),
            period="daily",
            adjustment="none",
            bar_aggregation=aggregation,
            limit=10,
            before_timestamp=None,
            now_ms=NOW,
        )
        assert result["barAggregation"] == aggregation


async def test_unknown_asset_type_rejected(fake_upstream):
    """未知资产类型无法路由 → UNSUPPORTED_CAPABILITY。"""
    with pytest.raises(ThsApiError) as exc:
        await bars.fetch_bars(
            fake_upstream,
            instrument=instrument("600519.SH", "something-new"),
            period="daily",
            adjustment="none",
            bar_aggregation="original",
            limit=10,
            before_timestamp=None,
            now_ms=NOW,
        )
    assert exc.value.kcq_code == "UNSUPPORTED_CAPABILITY"


async def test_missing_thscode_raises_invalid_request(fake_upstream):
    """无法解析 thscode → INVALID_REQUEST。"""
    with pytest.raises(ThsApiError) as exc:
        await bars.fetch_bars(
            fake_upstream,
            instrument={},
            period="daily",
            adjustment="none",
            bar_aggregation="original",
            limit=10,
            before_timestamp=None,
            now_ms=NOW,
        )
    assert exc.value.kcq_code == "INVALID_REQUEST"


async def test_missing_price_fields_tolerated(fake_upstream):
    """缺少可选字段（volume/turnover）不影响解析。"""
    fake_upstream.set_default(
        {"item": [{"date_ms": 1_797_000_000_000, "open_price": 1, "high_price": 2,
                   "low_price": 0.5, "close_price": 1.5}]}
    )
    result = await bars.fetch_bars(
        fake_upstream,
        instrument=instrument("600519.SH", "a-share"),
        period="daily",
        adjustment="none",
        bar_aggregation="original",
        limit=10,
        before_timestamp=None,
        now_ms=NOW,
    )
    assert result["items"][0]["close"] == 1.5
    assert "volume" not in result["items"][0]
