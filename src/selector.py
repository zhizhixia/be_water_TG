from __future__ import annotations

import logging
import random

logger = logging.getLogger(__name__)


class MessageSelector:
    """消息选择器，确保不连续发送相同消息。"""

    def __init__(self, messages: list[str]) -> None:
        """初始化消息选择器。

        Args:
            messages: 可选消息列表。

        Raises:
            ValueError: 消息列表为空时抛出。
        """
        if not messages:
            raise ValueError("消息列表不能为空")
        self._messages = messages
        self._last_message: str | None = None

    def select(self) -> str:
        """随机选择一条消息，确保不与上次相同。

        如果只有一条消息，则直接返回并记录警告。

        Returns:
            选中的消息字符串。
        """
        if len(self._messages) == 1:
            logger.warning("消息列表中只有一条消息，无法避免连续重复")
            return self._messages[0]

        candidates = [m for m in self._messages if m != self._last_message]
        chosen = random.choice(candidates)
        self._last_message = chosen
        return chosen
