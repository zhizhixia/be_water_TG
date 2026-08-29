from __future__ import annotations

from src.lottery_models import MessageSnapshot


def current_day_pinned(messages: list[MessageSnapshot], today: str, complete_enumeration: bool = True) -> list[MessageSnapshot]:
    """仅保留当前仍置顶且发生于上海当天的消息。

    API 无法完整枚举时，调用者传入的唯一最新置顶仍可安全扫描；该降级
    不推断其他历史置顶。
    """
    return [message for message in messages if message.pinned and message.occurred_at and message.occurred_at[:10] == today]
