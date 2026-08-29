from __future__ import annotations

from datetime import datetime, timedelta

from src.lottery_fingerprint import base_fingerprint, is_recent_duplicate, message_key, normalize_text
from src.lottery_models import ButtonSnapshot, MessageSnapshot


def make_message(
    peer_id: int = 100,
    message_id: int = 1,
    callback: bytes = b"join",
) -> MessageSnapshot:
    """构造用于指纹测试的不可变消息快照。"""
    return MessageSnapshot(
        peer_id=peer_id,
        message_id=message_id,
        text="ＡＢＣ\n  抽奖",
        buttons=((ButtonSnapshot("参加", "callback", callback),),),
    )


def test_normalize_text_uses_nfkc_and_collapses_whitespace() -> None:
    """正文规范化必须跨客户端稳定。"""
    assert normalize_text("ＡＢＣ\n  抽奖") == "ABC 抽奖"


def test_fingerprint_ignores_peer_but_includes_button_callback_digest() -> None:
    """基础指纹不含群号，但必须包含按钮回调摘要。"""
    assert base_fingerprint(make_message(peer_id=1)) == base_fingerprint(make_message(peer_id=2))
    assert base_fingerprint(make_message(callback=b"other")) != base_fingerprint(make_message())


def test_message_key_is_permanent_telegram_message_idempotency_key() -> None:
    """消息幂等键由稳定群号和消息号构成。"""
    assert message_key(make_message(peer_id=9, message_id=8)) == "9:8"


def test_duplicate_window_is_conservative_for_missing_deadline_and_expires_after_seven_days() -> None:
    """截止时间缺失保守去重，明确同截止仅在七天窗口内重复。"""
    now = datetime(2026, 8, 29, 12, 0, 0)
    assert is_recent_duplicate("same", None, "same", "2026-09-01T00:00:00", now, now - timedelta(days=8))
    assert is_recent_duplicate("same", "2026-09-01T00:00:00", "same", "2026-09-01T00:00:00", now, now - timedelta(days=6, seconds=1))
    assert not is_recent_duplicate("same", "2026-09-01T00:00:00", "same", "2026-09-02T00:00:00", now, now - timedelta(days=1))
    assert not is_recent_duplicate("same", "2026-09-01T00:00:00", "same", "2026-09-01T00:00:00", now, now - timedelta(days=7))
