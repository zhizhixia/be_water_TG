from __future__ import annotations

import pytest

from src.group_parser import (
    normalize_group_link,
    parse_group_links,
    validate_group_links,
)


class TestParseGroupLinks:
    """parse_group_links 函数测试。"""

    def test_english_comma_separated(self):
        """英文逗号分隔。"""
        result = parse_group_links("@group1,@group2,@group3")
        assert result == [
            "https://t.me/group1",
            "https://t.me/group2",
            "https://t.me/group3",
        ]

    def test_chinese_comma_separated(self):
        """中文逗号分隔。"""
        result = parse_group_links("@group1，@group2，@group3")
        assert result == [
            "https://t.me/group1",
            "https://t.me/group2",
            "https://t.me/group3",
        ]

    def test_mixed_comma_separated(self):
        """中英文逗号混合分隔。"""
        result = parse_group_links("@group1，@group2,@group3，@group4")
        assert result == [
            "https://t.me/group1",
            "https://t.me/group2",
            "https://t.me/group3",
            "https://t.me/group4",
        ]

    def test_empty_string(self):
        """空字符串输入返回空列表。"""
        result = parse_group_links("")
        assert result == []

    def test_blank_entries_filtered(self):
        """空白条目被过滤。"""
        result = parse_group_links("@group1, ,@group2,  ,")
        assert result == [
            "https://t.me/group1",
            "https://t.me/group2",
        ]

    def test_deduplicates_preserving_order(self):
        """去重并保留首次出现顺序。"""
        result = parse_group_links("@a,@b,@a,@c,@b")
        assert result == [
            "https://t.me/a",
            "https://t.me/b",
            "https://t.me/c",
        ]

    def test_normalizes_various_formats(self):
        """多种链接格式被统一标准化。"""
        result = parse_group_links("t.me/group1,@group2,https://t.me/group3")
        assert result == [
            "https://t.me/group1",
            "https://t.me/group2",
            "https://t.me/group3",
        ]


class TestNormalizeGroupLink:
    """normalize_group_link 函数测试。"""

    def test_at_username(self):
        """@username 格式转换为 https://t.me/username。"""
        result = normalize_group_link("@telegram_group")
        assert result == "https://t.me/telegram_group"

    def test_t_me_username(self):
        """t.me/username 格式转换为 https://t.me/username。"""
        result = normalize_group_link("t.me/mygroup")
        assert result == "https://t.me/mygroup"

    def test_https_t_me_unchanged(self):
        """https://t.me/username 格式保持不变。"""
        result = normalize_group_link("https://t.me/mygroup")
        assert result == "https://t.me/mygroup"

    def test_unknown_format_returns_as_is(self):
        """无法识别的格式原样返回。"""
        result = normalize_group_link("some_random_string")
        assert result == "some_random_string"


class TestValidateGroupLinks:
    """validate_group_links 函数测试。"""

    def test_empty_list_raises_value_error(self):
        """空列表抛出 ValueError。"""
        with pytest.raises(ValueError, match="至少需要一个目标群组"):
            validate_group_links([])

    def test_invalid_url_format_raises_value_error(self):
        """无效链接格式抛出 ValueError。"""
        with pytest.raises(ValueError, match="无效的群组链接"):
            validate_group_links(["not_a_url"])

    def test_valid_links_no_exception(self):
        """有效链接不抛出异常。"""
        validate_group_links([
            "https://t.me/group1",
            "https://t.me/group2",
        ])  # 不应抛出异常
