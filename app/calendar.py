"""交易日历层：同花顺交易日序列 → KCQ 未来交易槽位。

上游 /api/a-share/calendar/trading-days 无参数，固定返回近一年交易日。
"""
from __future__ import annotations

from .upstream import ThsUpstream

PATH_TRADING_DAYS = "/api/a-share/calendar/trading-days"


async def fetch_trading_calendar(
    upstream: ThsUpstream, anchor_timestamp: int, count: int
) -> dict:
    """返回 anchor 之后的 count 个交易日毫秒戳（升序）。

    上游仅提供近一年交易日；anchor 之后不足 count 个时按实际数量返回。
    """
    envelope = await upstream.get(PATH_TRADING_DAYS)
    items = (envelope.data or {}).get("item") or []
    timestamps = sorted(
        int(item["date_ms"])
        for item in items
        if isinstance(item, dict) and isinstance(item.get("date_ms"), (int, float))
    )
    future = [ts for ts in timestamps if ts > anchor_timestamp]
    return {"anchorTimestamp": anchor_timestamp, "futureTimestamps": future[:count]}
