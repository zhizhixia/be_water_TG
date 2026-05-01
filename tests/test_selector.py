from __future__ import annotations

import pytest

from src.selector import MessageSelector


class TestMessageSelector:
    """MessageSelector 类测试。"""

    def test_select_no_consecutive_duplicate(self):
        """连续选择不重复（统计测试）。"""
        selector = MessageSelector(["A", "B", "C", "D"])
        results = [selector.select() for _ in range(50)]
        for i in range(len(results) - 1):
            assert results[i] != results[i + 1], (
                f"连续重复: {results[i]} 在位置 {i} 和 {i + 1}"
            )

    def test_select_single_message(self):
        """单条消息处理。"""
        selector = MessageSelector(["only_one"])
        result = selector.select()
        assert result == "only_one"

    def test_selector_empty_list(self):
        """空列表拒绝。"""
        with pytest.raises(ValueError, match="消息列表不能为空"):
            MessageSelector([])

    def test_select_uniform_distribution(self):
        """均匀分布验证（简单频率检查）。"""
        messages = ["A", "B", "C", "D"]
        selector = MessageSelector(messages)
        counts = {m: 0 for m in messages}
        n = 1000
        for _ in range(n):
            chosen = selector.select()
            counts[chosen] += 1

        # 每个消息应该被选中至少 15% 的次数（均匀分布约 25%）
        for msg in messages:
            assert counts[msg] >= n * 0.15, (
                f"消息 '{msg}' 选择频率过低: {counts[msg]}/{n}"
            )
