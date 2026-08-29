from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta

from src.lottery_models import MessageSnapshot


def normalize_text(text: str) -> str:
    """使用 NFKC 和单空格生成跨客户端稳定的正文。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def base_fingerprint(message: MessageSnapshot) -> str:
    """生成不含群号和截止时间、含按钮布局与回调摘要的 SHA-256 指纹。"""
    parts = [normalize_text(message.text)]
    for row_index, row in enumerate(message.buttons):
        for column_index, button in enumerate(row):
            callback_digest = hashlib.sha256(button.callback_data or b"").hexdigest()
            parts.append(
                f"{row_index}:{column_index}:{normalize_text(button.text)}:"
                f"{button.kind}:{callback_digest}"
            )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def message_key(message: MessageSnapshot) -> str:
    """返回 Telegram 消息永久幂等键。"""
    return f"{message.peer_id}:{message.message_id}"


def is_recent_duplicate(
    candidate_fingerprint: str,
    candidate_deadline: str | None,
    existing_fingerprint: str,
    existing_deadline: str | None,
    now: datetime,
    existing_created_at: datetime,
) -> bool:
    """按七天窗口与截止语义保守判断跨群内容重复。"""
    if candidate_fingerprint != existing_fingerprint:
        return False
    if candidate_deadline is None or existing_deadline is None:
        return True
    if now - existing_created_at >= timedelta(days=7):
        return False
    return candidate_deadline == existing_deadline
