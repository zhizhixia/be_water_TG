from __future__ import annotations

import pytest

from src.interval import format_duration, get_random_interval


class TestGetRandomInterval:
    """get_random_interval 函数测试。"""

    def test_get_random_interval_in_range(self):
        """返回值在范围内。"""
        results = [get_random_interval(60, 180) for _ in range(200)]
        for r in results:
            assert 60 <= r <= 180, f"值 {r} 超出范围 [60, 180]"

    def test_get_random_interval_equal(self):
        """min == max 时返回固定值。"""
        result = get_random_interval(100, 100)
        assert result == 100

    def test_get_random_interval_invalid(self):
        """min > max 时抛出 ValueError。"""
        with pytest.raises(ValueError, match="最小间隔"):
            get_random_interval(200, 100)


class TestFormatDuration:
    """format_duration 函数测试。"""

    def test_format_duration_seconds_only(self):
        """仅秒数。"""
        assert format_duration(45) == "45秒"

    def test_format_duration_minutes_and_seconds(self):
        """分钟和秒。"""
        assert format_duration(90) == "1分30秒"

    def test_format_duration_hours(self):
        """小时、分钟和秒。"""
        assert format_duration(3600) == "1小时0分0秒"

    def test_format_duration_zero(self):
        """零秒。"""
        assert format_duration(0) == "0秒"

    def test_format_duration_complex(self):
        """复杂时间。"""
        assert format_duration(3661) == "1小时1分1秒"
