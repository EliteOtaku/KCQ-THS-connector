"""HTTP 路由层：KCQ market-data V1 协议端点。

成功与业务错误一律返回 HTTP 200，错误经 KCQ 错误信封的 code 表达
（与 KCQ 侧 SourceRouter 的解析契约一致）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import bars as bars_mod
from . import calendar as calendar_mod
from . import catalog as catalog_mod
from .envelope import fail, ok
from .errors import INVALID_REQUEST, UNSUPPORTED_CAPABILITY, http_status_for
from .upstream import ThsApiError

router = APIRouter()

V1_BASE = "/api/v1/market-data"


def _now_ms() -> int:
    """当前 UTC 毫秒。"""
    return int(time.time() * 1000)


def _get_upstream(request: Request):
    """从应用状态取上游客户端。"""
    return request.app.state.upstream


def _get_settings(request: Request):
    """从应用状态取配置。"""
    return request.app.state.settings


@router.get(f"{V1_BASE}/sources/{{source_id}}/probe")
async def probe(source_id: str, request: Request):
    """探测数据源可用性并声明能力。

    能力必须写实：只声明 daily。KCQ SourceRouter 据此在请求 1min 时
    自动流转到其它源，无需任何特判。
    """
    if source_id != catalog_mod.SOURCE_ID:
        return JSONResponse(status_code=404, content=fail(INVALID_REQUEST, "未知数据源"))

    settings = _get_settings(request)
    capabilities = {
        "assetClasses": ["stock", "index", "etf", "fund", "future", "option"],
        "bars": {"periods": ["daily"], "adjustments": ["none", "qfq", "hfq"]},
        "tradingCalendar": True,
    }

    if not settings.has_api_key:
        return ok(
            {
                "status": "degraded",
                "checkedAt": _now_ms(),
                "message": "未配置 THS_API_KEY",
                "capabilities": capabilities,
            }
        )

    upstream = _get_upstream(request)
    started = time.perf_counter()
    try:
        await upstream.get(calendar_mod.PATH_TRADING_DAYS)
    except ThsApiError as exc:
        return ok(
            {
                "status": "degraded",
                "checkedAt": _now_ms(),
                "message": exc.message,
                "capabilities": capabilities,
            }
        )
    latency = int((time.perf_counter() - started) * 1000)
    return ok(
        {
            "status": "online",
            "checkedAt": _now_ms(),
            "latencyMs": latency,
            "capabilities": capabilities,
        }
    )


@router.post(f"{V1_BASE}/instruments/search")
async def search_instruments(request: Request):
    """品种目录检索。"""
    body = await request.json()
    upstream = _get_upstream(request)
    result = await catalog_mod.search_instruments(
        upstream,
        keyword=str(body.get("keyword", "") or ""),
        limit=int(body.get("limit", 20) or 20),
        asset_classes=list(body.get("assetClasses") or []) or None,
    )
    return ok(result)


@router.post(f"{V1_BASE}/bars")
async def bars(request: Request):
    """K 线（仅日线）。"""
    body = await request.json()
    upstream = _get_upstream(request)
    result = await bars_mod.fetch_bars(
        upstream,
        instrument=body.get("instrument") or {},
        period=str(body.get("period", "") or ""),
        adjustment=str(body.get("adjustment", "none") or "none"),
        bar_aggregation=str(body.get("barAggregation", "original") or "original"),
        limit=int(body.get("limit", 100) or 100),
        before_timestamp=body.get("beforeTimestamp"),
        now_ms=_now_ms(),
    )
    return ok(result)


@router.post(f"{V1_BASE}/trading-calendar")
async def trading_calendar(request: Request):
    """未来交易槽位。"""
    body = await request.json()
    upstream = _get_upstream(request)
    result = await calendar_mod.fetch_trading_calendar(
        upstream,
        anchor_timestamp=int(body.get("anchorTimestamp", 0) or 0),
        count=int(body.get("count", 0) or 0),
    )
    return ok(result)


@router.post(f"{V1_BASE}/timeshare")
async def timeshare(request: Request):
    """分时：P0 未实现（同花顺端外不提供 A 股分时）。"""
    return JSONResponse(
        status_code=http_status_for(UNSUPPORTED_CAPABILITY),
        content=fail(
            UNSUPPORTED_CAPABILITY,
            "本连接器不提供分时：同花顺端外未开放 A 股分钟/分时数据",
        ),
    )


@router.get("/health/live")
async def health_live():
    """存活探针。"""
    return ok({"status": "alive"})


@router.get("/health/ready")
async def health_ready(request: Request):
    """就绪探针：报告是否已配置 key。"""
    settings = _get_settings(request)
    return ok({"status": "ready" if settings.has_api_key else "degraded"})
