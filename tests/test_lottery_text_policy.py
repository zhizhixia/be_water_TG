from __future__ import annotations

import pytest

from src.lottery_text_policy import is_safe_fixed_text


@pytest.mark.parametrize("text", ["+1", "我参加 🔥", "签到，感谢！", "A" * 32])
def test_safe_fixed_text_accepts_short_human_fixed_text(text: str) -> None:
    """短固定文本允许中英文、标点、数字和 emoji。"""
    assert is_safe_fixed_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "A" * 33,
        "https://example.com",
        "www.example.com",
        "@someone",
        "13800138000",
        "me@example.com",
        "参与\n谢谢",
        "参与\u2028谢谢",
        "参与\x00",
        "参与\u200b",
    ],
)
def test_safe_fixed_text_rejects_links_identity_and_control_characters(text: str) -> None:
    """文本策略必须拒绝外联、身份信息和全部控制字符。"""
    assert not is_safe_fixed_text(text)
