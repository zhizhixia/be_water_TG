from __future__ import annotations

import random


def get_random_interval(min_seconds: int, max_seconds: int) -> int:
    """生成随机间隔秒数。

    Args:
        min_seconds: 最小间隔秒数。
        max_seconds: 最大间隔秒数。

    Returns:
        [min_seconds, max_seconds] 范围内的随机整数。

    Raises:
        ValueError: min_seconds > max_seconds 时抛出。
    """
    if min_seconds > max_seconds:
        raise ValueError(
            f"最小间隔 ({min_seconds}) 不能大于最大间隔 ({max_seconds})"
        )
    return random.randint(min_seconds, max_seconds)


def format_duration(seconds: int) -> str:
    """将秒数格式化为人类可读的中文时间字符串。

    Args:
        seconds: 秒数。

    Returns:
        格式化后的时间字符串，如 "1分30秒"、"1小时0分0秒"、"45秒"。
    """
    if seconds < 60:
        return f"{seconds}秒"

    hours = seconds // 3600
    remaining = seconds % 3600
    minutes = remaining // 60
    secs = remaining % 60

    if hours > 0:
        return f"{hours}小时{minutes}分{secs}秒"
    return f"{minutes}分{secs}秒"
