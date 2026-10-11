"""品种目录层：同花顺标的检索/代码表 → KCQ 品种描述。

职责：asset_type → KCQ AssetClass 映射、descriptor 构造、会话与复权能力声明。
"""
from __future__ import annotations

from .upstream import ThsUpstream

# 本连接器在 KCQ 侧的数据源 ID
SOURCE_ID = "ths"

# 上游检索端点
PATH_SEARCH = "/api/meta/tickers/search"
PATH_LIST = "/api/meta/tickers/list"

# 上游 asset_type → KCQ AssetClass
# 注意 KCQ 的合法值只有 stock|index|fund|etf|future|option|forex|crypto|unknown，
# 没有 fund-otc，故归一到 fund。
ASSET_TYPE_TO_CLASS: dict[str, str] = {
    "a-share": "stock",
    "a-share-index": "index",
    "fund-etf": "etf",
    "fund-otc": "fund",
    "futures": "future",
    "options": "option",
}

# 复用 KCQ 内置会话的资产类别（CN = 沪深A股时段，已内置，无需新增 marketSessions）
_CN_SESSION_CLASSES = frozenset({"stock", "index", "etf"})

# 各资产类别的复权能力：仅 A 股支持复权
_ADJUSTABLE_CLASSES = frozenset({"stock"})
_FULL_ADJUSTMENTS = ("none", "qfq", "hfq")
_NONE_ADJUSTMENTS = ("none",)


def asset_class_of(asset_type: str | None) -> str:
    """上游 asset_type → KCQ AssetClass；未知类型归为 unknown。"""
    if not asset_type:
        return "unknown"
    return ASSET_TYPE_TO_CLASS.get(asset_type, "unknown")


def adjustments_of(asset_class: str) -> list[str]:
    """按资产类别返回可用复权方式（仅 A 股支持复权）。"""
    return list(_FULL_ADJUSTMENTS if asset_class in _ADJUSTABLE_CLASSES else _NONE_ADJUSTMENTS)


def session_id_of(asset_class: str) -> str | None:
    """复用 KCQ 内置 CN 会话；期货/期权时段与股票不同，留空。"""
    return "CN" if asset_class in _CN_SESSION_CLASSES else None


def build_descriptor(item: dict) -> dict:
    """上游 ticker 记录 → KCQ ProtocolInstrumentDescriptor。"""
    thscode = str(item.get("thscode", ""))
    asset_type = item.get("asset_type")
    asset_class = asset_class_of(asset_type)
    exchange = item.get("exchange")
    currency = item.get("currency")
    descriptor: dict = {
        "id": f"{SOURCE_ID}:{thscode}",
        "sourceId": SOURCE_ID,
        "symbol": thscode,
        "name": str(item.get("name", "") or ""),
        "assetClass": asset_class,
        "exchange": exchange if isinstance(exchange, str) else "",
        "currency": currency if isinstance(currency, str) else None,
        # providerRef 是唯一路由依据：KCQ 只原样带回，不解析
        "providerRef": {"thscode": thscode, "assetType": asset_type or ""},
        "capabilities": {
            "bars": {"periods": ["daily"], "adjustments": adjustments_of(asset_class)}
        },
    }
    session_id = session_id_of(asset_class)
    if session_id is not None:
        descriptor["sessionId"] = session_id
    return descriptor


async def search_instruments(
    upstream: ThsUpstream,
    keyword: str,
    limit: int,
    asset_classes: list[str] | None = None,
) -> dict:
    """按关键词检索品种；上游不支持按类别过滤，取回后在本地过滤。"""
    if not keyword:
        # 上游 q 必填，空关键词直接返回空结果，避免无谓的 1001
        return {"items": []}
    envelope = await upstream.get(PATH_SEARCH, {"q": keyword, "limit": limit})
    items = (envelope.data or {}).get("item") or []
    descriptors = [build_descriptor(item) for item in items if isinstance(item, dict)]
    if asset_classes:
        wanted = set(asset_classes)
        descriptors = [d for d in descriptors if d["assetClass"] in wanted]
    return {"items": descriptors}


async def list_tickers(
    upstream: ThsUpstream, asset_type: str, limit: int, offset: int
) -> dict:
    """按资产类型分页取代码表。"""
    envelope = await upstream.get(
        PATH_LIST, {"asset_type": asset_type, "limit": limit, "offset": offset}
    )
    items = (envelope.data or {}).get("item") or []
    descriptors = [build_descriptor(item) for item in items if isinstance(item, dict)]
    return {"items": descriptors}
