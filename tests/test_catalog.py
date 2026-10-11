"""目录层测试：asset_type 映射、descriptor 结构、会话与复权能力。"""
from __future__ import annotations

import pytest

from app import catalog


@pytest.mark.parametrize(
    ("asset_type", "expected_class"),
    [
        ("a-share", "stock"),
        ("a-share-index", "index"),
        ("fund-etf", "etf"),
        ("fund-otc", "fund"),
        ("futures", "future"),
        ("options", "option"),
        ("something-new", "unknown"),
        (None, "unknown"),
    ],
)
def test_asset_type_mapping(asset_type, expected_class):
    """六种上游类型映射到 KCQ AssetClass；未知归 unknown。"""
    assert catalog.asset_class_of(asset_type) == expected_class


def test_kcq_asset_class_values_are_legal():
    """映射结果必须落在 KCQ 合法值域内（否则会被静默过滤）。"""
    legal = {"stock", "index", "fund", "etf", "future", "option", "forex", "crypto", "unknown"}
    for asset_type in ["a-share", "a-share-index", "fund-etf", "fund-otc", "futures",
                       "options", "nope", None]:
        assert catalog.asset_class_of(asset_type) in legal


@pytest.mark.parametrize(
    ("asset_class", "expected_session"),
    [
        ("stock", "CN"),
        ("index", "CN"),
        ("etf", "CN"),
        ("fund", None),
        ("future", None),
        ("option", None),
        ("unknown", None),
    ],
)
def test_session_id_mapping(asset_class, expected_session):
    """复用 KCQ 内置 CN 会话；期货/期权时段不同，留空。"""
    assert catalog.session_id_of(asset_class) == expected_session


@pytest.mark.parametrize(
    ("asset_class", "expected"),
    [
        ("stock", ["none", "qfq", "hfq"]),
        ("index", ["none"]),
        ("etf", ["none"]),
        ("future", ["none"]),
        ("option", ["none"]),
    ],
)
def test_adjustments_mapping(asset_class, expected):
    """仅 A 股支持复权。"""
    assert catalog.adjustments_of(asset_class) == expected


def test_build_descriptor_structure():
    """descriptor 字段齐全且 id/providerRef 正确。"""
    descriptor = catalog.build_descriptor(
        {
            "thscode": "600519.SH",
            "ticker": "600519",
            "name": "贵州茅台",
            "exchange": "SH",
            "asset_type": "a-share",
            "currency": "CNY",
        }
    )
    assert descriptor["id"] == "ths:600519.SH"
    assert descriptor["sourceId"] == "ths"
    assert descriptor["symbol"] == "600519.SH"
    assert descriptor["name"] == "贵州茅台"
    assert descriptor["assetClass"] == "stock"
    assert descriptor["exchange"] == "SH"
    assert descriptor["currency"] == "CNY"
    assert descriptor["sessionId"] == "CN"
    # providerRef 是唯一路由依据
    assert descriptor["providerRef"] == {"thscode": "600519.SH", "assetType": "a-share"}
    assert descriptor["capabilities"]["bars"]["periods"] == ["daily"]


def test_build_descriptor_handles_null_exchange_and_currency():
    """上游 exchange/currency 可能为 null → 用 ""/None。"""
    descriptor = catalog.build_descriptor(
        {"thscode": "885525.TI", "name": "白酒概念", "exchange": None,
         "asset_type": "a-share-index", "currency": None}
    )
    assert descriptor["exchange"] == ""
    assert descriptor["currency"] is None


def test_build_descriptor_omits_session_for_futures():
    """期货不设 sessionId 字段。"""
    descriptor = catalog.build_descriptor(
        {"thscode": "IC2612.CFE", "name": "中证500 2612", "exchange": "CFFEX",
         "asset_type": "futures"}
    )
    assert "sessionId" not in descriptor


async def test_search_empty_keyword_skips_upstream(fake_upstream):
    """空关键词直接返回空列表，不打上游（避免无谓的 1001）。"""
    result = await catalog.search_instruments(fake_upstream, keyword="", limit=20)
    assert result == {"items": []}
    assert fake_upstream.calls == []


async def test_search_returns_descriptors(fake_upstream):
    """检索返回 descriptor 列表。"""
    fake_upstream.set_response(
        catalog.PATH_SEARCH,
        {"item": [{"thscode": "600519.SH", "name": "贵州茅台", "exchange": "SH",
                   "asset_type": "a-share", "currency": "CNY"}]},
    )
    result = await catalog.search_instruments(fake_upstream, keyword="茅台", limit=20)
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "ths:600519.SH"


async def test_search_filters_by_asset_class_locally(fake_upstream):
    """上游不支持类别过滤 → 本地过滤。"""
    fake_upstream.set_response(
        catalog.PATH_SEARCH,
        {
            "item": [
                {"thscode": "600519.SH", "name": "茅台", "exchange": "SH",
                 "asset_type": "a-share", "currency": "CNY"},
                {"thscode": "000300.SH", "name": "沪深300", "exchange": "SH",
                 "asset_type": "a-share-index", "currency": "CNY"},
            ]
        },
    )
    result = await catalog.search_instruments(
        fake_upstream, keyword="x", limit=20, asset_classes=["index"]
    )
    assert [d["id"] for d in result["items"]] == ["ths:000300.SH"]


async def test_list_tickers_passes_paging(fake_upstream):
    """代码表分页参数透传。"""
    fake_upstream.set_response(catalog.PATH_LIST, {"item": []})
    await catalog.list_tickers(fake_upstream, asset_type="futures", limit=5, offset=10)
    params = fake_upstream.calls_for(catalog.PATH_LIST)[0]
    assert params == {"asset_type": "futures", "limit": 5, "offset": 10}
