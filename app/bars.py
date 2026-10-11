"""K 线层：游标 → 区间翻译、域路由、量纲判定与响应构造。

本模块承载同花顺最主要的域特异性：五域不可混用、时间字段名不一致、
成交量量纲差异、复权仅 A 股支持。
"""
from __future__ import annotations

import math

from .errors import INVALID_REQUEST, UNSUPPORTED_CAPABILITY
from .upstream import ThsApiError, ThsUpstream

DAY_MS = 86_400_000
# 上游硬限：start/end 跨度不得超过 10 年（超过返回 1003）
MAX_WINDOW_DAYS = 3650
# A 股约 242 交易日/年 → 1 交易日 ≈ 1.51 自然日；取 1.6 倍并加余量
_TRADING_DAY_TO_CALENDAR = 1.6
_WINDOW_MARGIN_DAYS = 10

# 仅支持日线（同花顺端外无分钟级行情）
SUPPORTED_PERIOD = "daily"

# 上游 asset_type → (历史K线端点, 时间字段名, 是否支持 adjust 参数, 周期参数名, 周期值)
#
# 周期参数名与"是否必填"因域而异（实测）：
#   - A股 / 指数：`interval` **必填**，缺失返回 1001 Missing required parameter: interval
#   - ETF：`interval` 可选（显式传更稳）
#   - 期货 / 期权：`time_period`，端外仅允许 day_1
# 一律显式传值，避免依赖上游默认。
ROUTES: dict[str, tuple[str, str, bool, str, str]] = {
    "a-share": (
        "/api/a-share/prices/historical", "date_ms", True, "interval", "1d",
    ),
    "a-share-index": (
        "/api/a-share-index/prices/historical", "date_ms", False, "interval", "1d",
    ),
    "fund-etf": (
        "/api/fund/market/historical", "date_ms", False, "interval", "1d",
    ),
    "futures": (
        "/api/futures/prices/daily", "timestamp", False, "time_period", "day_1",
    ),
    "options": (
        "/api/options/prices/daily", "timestamp", False, "time_period", "day_1",
    ),
}

# 上游 asset_type → 成交量单位（实测所得，见 docs/UPSTREAM_CONTRACT.md §4）
# A股/指数/ETF 为「股」，期货/期权为「手」。
VOLUME_UNITS: dict[str, str] = {
    "a-share": "share",
    "a-share-index": "share",
    "fund-etf": "share",
    "futures": "contract",
    "options": "contract",
}

# KCQ KLineAdjustment → 上游 adjust 参数
ADJUSTMENT_TO_UPSTREAM: dict[str, str] = {
    "none": "none",
    "qfq": "forward",
    "hfq": "backward",
}

# 支持复权的上游资产类型（仅 A 股）
_ADJUSTABLE_ASSET_TYPES = frozenset({"a-share"})

# 时区恒为上海（上游时间戳均按 Asia/Shanghai 解释）
TIMEZONE = "Asia/Shanghai"


def resolve_window(
    limit: int, before_timestamp: int | None, now_ms: int
) -> tuple[int, int, bool]:
    """把 KCQ 的 limit + 排他游标翻译成上游的 [start, end] 闭区间。

    返回 (start, end, capped)。capped=True 表示已顶到上游 10 年上限。

    排他语义：上游区间含两端，故 end = before_timestamp - 1。
    不传游标时 end = now_ms（返回最新一页）。
    """
    end = (before_timestamp - 1) if before_timestamp is not None else now_ms
    needed_days = math.ceil(limit * _TRADING_DAY_TO_CALENDAR) + _WINDOW_MARGIN_DAYS
    window_days = min(needed_days, MAX_WINDOW_DAYS)
    start = end - window_days * DAY_MS
    return start, end, window_days >= MAX_WINDOW_DAYS


def parse_thscode(instrument: dict) -> tuple[str, str]:
    """从 KCQ instrument 引用中解析 (thscode, asset_type)。

    优先读 providerRef（数据源私有路由参数）；回退到 symbol。
    """
    provider_ref = instrument.get("providerRef")
    if isinstance(provider_ref, dict):
        thscode = provider_ref.get("thscode")
        asset_type = provider_ref.get("assetType")
        if isinstance(thscode, str) and thscode:
            return thscode, asset_type if isinstance(asset_type, str) else ""
    symbol = instrument.get("symbol")
    if isinstance(symbol, str) and symbol:
        # 无 assetType 时无法确定域路由，交由调用方校验
        return symbol, ""
    raise ThsApiError(INVALID_REQUEST, "无法从 instrument 解析出 thscode")


def _validate(period: str, adjustment: str, asset_type: str) -> None:
    """请求前置校验：不支持的周期/复权一律拒绝，避免返回错数据。"""
    if period != SUPPORTED_PERIOD:
        raise ThsApiError(
            UNSUPPORTED_CAPABILITY,
            f"同花顺端外仅支持日线，不支持 {period}",
        )
    if adjustment == "splits":
        raise ThsApiError(UNSUPPORTED_CAPABILITY, "同花顺不支持 splits 复权")
    if adjustment not in ADJUSTMENT_TO_UPSTREAM:
        raise ThsApiError(INVALID_REQUEST, f"未知复权方式：{adjustment}")
    if adjustment != "none" and asset_type not in _ADJUSTABLE_ASSET_TYPES:
        # 指数/ETF/期货/期权无复权概念：拒绝而非忽略参数返回未复权数据
        raise ThsApiError(
            UNSUPPORTED_CAPABILITY,
            f"{asset_type or '该资产类型'} 不支持复权",
        )


def _parse_item(raw: dict, time_field: str) -> dict | None:
    """上游 K 线记录 → KCQ ProtocolKLineItem；字段缺失返回 None。

    时间戳直接透传，不做时区换算（上游已是真正的 epoch 毫秒）。
    """
    timestamp = raw.get(time_field)
    if not isinstance(timestamp, (int, float)):
        return None
    item: dict = {
        "timestamp": int(timestamp),
        "open": raw.get("open_price"),
        "high": raw.get("high_price"),
        "low": raw.get("low_price"),
        "close": raw.get("close_price"),
    }
    volume = raw.get("volume")
    if isinstance(volume, (int, float)):
        item["volume"] = volume
    turnover = raw.get("turnover")
    if isinstance(turnover, (int, float)):
        item["turnover"] = turnover
    return item


async def fetch_bars(
    upstream: ThsUpstream,
    instrument: dict,
    period: str,
    adjustment: str,
    bar_aggregation: str,
    limit: int,
    before_timestamp: int | None,
    now_ms: int,
) -> dict:
    """取日线：校验 → 翻译区间 → 路由取数 → 解析 → 组装 KCQ 响应。"""
    thscode, asset_type = parse_thscode(instrument)
    _validate(period, adjustment, asset_type)

    route = ROUTES.get(asset_type)
    if route is None:
        raise ThsApiError(
            UNSUPPORTED_CAPABILITY, f"不支持的资产类型：{asset_type or '未知'}"
        )
    path, time_field, supports_adjust, period_param, period_value = route

    start, end, capped = resolve_window(limit, before_timestamp, now_ms)
    params: dict = {
        "thscode": thscode,
        "start": start,
        "end": end,
        # 周期参数名因域而异，且 A股/指数必填；一律显式传
        period_param: period_value,
    }
    if supports_adjust:
        # 必须显式传：上游默认 forward，不传会让 none 请求静默拿到前复权数据
        params["adjust"] = ADJUSTMENT_TO_UPSTREAM[adjustment]

    envelope = await upstream.get(path, params)
    raw_items = (envelope.data or {}).get("item") or []
    items = [
        parsed
        for parsed in (_parse_item(raw, time_field) for raw in raw_items if isinstance(raw, dict))
        if parsed is not None
    ]
    # 取窗口内最后 limit 根（上游按时间升序返回）
    items = items[-limit:] if limit > 0 else []

    # olderData：顶到上游上限、或本页不足一页 → 没有更多历史
    older_data = "exhausted" if (capped or len(items) < limit) else "available"

    return {
        "instrumentId": instrument.get("id", ""),
        "period": period,
        "adjustment": adjustment,
        "barAggregation": bar_aggregation,
        "timezone": TIMEZONE,
        # 必须显式返回：KCQ 内置兜底会把 CN 非指数品种当 lot(手)，
        # 而同花顺 A 股是股 → 不覆盖会放大 100 倍且图形无异常
        "volumeUnit": VOLUME_UNITS.get(asset_type, "share"),
        "items": items,
        "olderData": older_data,
    }
