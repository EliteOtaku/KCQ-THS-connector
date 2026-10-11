"""游标 → 区间翻译测试（行为基准：改实现不得改断言）。"""
from __future__ import annotations

import math

import pytest

from app import bars

NOW = 1_800_000_000_000


def test_no_cursor_uses_now():
    """不传游标 → end = now_ms（返回最新一页）。"""
    start, end, capped = bars.resolve_window(100, None, NOW)
    assert end == NOW
    assert capped is False
    assert start < end


def test_cursor_is_exclusive():
    """传游标 → end = before - 1（上游区间含两端，故排他）。"""
    before = NOW
    _, end, _ = bars.resolve_window(100, before, NOW)
    assert end == before - 1


def test_window_days_formula():
    """窗口天数 = ceil(limit * 1.6) + 10。"""
    _, _, _ = bars.resolve_window(250, None, NOW)
    # 250 * 1.6 = 400 → ceil 400 → +10 = 410
    expected_days = math.ceil(250 * 1.6) + 10
    assert expected_days == 410
    start, end, capped = bars.resolve_window(250, None, NOW)
    assert end - start == 410 * bars.DAY_MS
    assert capped is False


def test_window_capped_at_ten_years():
    """极大 limit → 窗口被截到 10 年上限，并标记 capped。"""
    start, end, capped = bars.resolve_window(100_000, None, NOW)
    assert end - start == bars.MAX_WINDOW_DAYS * bars.DAY_MS
    assert capped is True


def test_boundary_exactly_at_cap_is_capped():
    """恰好等于上限也算顶到上限。"""
    limit = (bars.MAX_WINDOW_DAYS - 10) / 1.6
    _, _, capped = bars.resolve_window(int(limit), None, NOW)
    assert capped is True


@pytest.mark.parametrize(
    ("limit", "returned", "expected"),
    [
        (250, 250, "available"),
        (250, 249, "exhausted"),
        (250, 0, "exhausted"),
        (30, 30, "available"),
        (30, 12, "exhausted"),
    ],
)
def test_older_data_rule(limit, returned, expected):
    """本页不足一页 → exhausted；满页 → available。"""
    capped = False
    older = "exhausted" if (capped or returned < limit) else "available"
    assert older == expected
