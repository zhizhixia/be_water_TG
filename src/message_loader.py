from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_messages(file_path: str) -> list[str]:
    """从文本文件加载消息列表。

    文件内容按英文逗号或中文逗号分隔，自动去除首尾空白并过滤空字符串。

    Args:
        file_path: 消息文件路径。

    Returns:
        消息字符串列表。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"消息文件不存在: {file_path}")

    content = path.read_text(encoding="utf-8")
    return parse_messages(content)


def parse_messages(content: str) -> list[str]:
    """解析逗号分隔的消息文本并过滤空项。"""
    normalized = content.replace("，", ",")
    return [message for item in normalized.split(",") if (message := item.strip())]


def validate_messages(messages: list[str]) -> None:
    """校验消息列表是否有效。

    Args:
        messages: 消息列表。

    Raises:
        ValueError: 消息列表为空时抛出。
    """
    if not messages:
        raise ValueError("消息列表不能为空，请确保消息文件中至少有一条有效消息")
