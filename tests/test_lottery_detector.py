from __future__ import annotations

import pytest

from src.lottery_detector import detect, detect_with_ai
from src.lottery_models import ButtonSnapshot, MessageSnapshot


def message(text: str, *, source: str = "incremental", pinned: bool = False, occurred_at: str | None = None) -> MessageSnapshot:
    """构造仅含安全文本和按钮的消息快照。"""
    return MessageSnapshot(1, 2, text, ((ButtonSnapshot("参与", "callback", b"join"),),), occurred_at=occurred_at, source=source, pinned=pinned)


def test_rule_complete_lottery_is_certain_and_ended_is_rejected() -> None:
    """明确抽奖组合由规则决定，无需 AI。"""
    assert detect(message("抽奖 奖品耳机，截止时间今晚，点击参与")).certain
    ended = detect(message("抽奖结束，感谢参与"))
    assert not ended.is_lottery and ended.certain


def test_missing_deadline_requires_recent_incremental_or_today_pinned() -> None:
    """无截止只能由最近一小时增量或当日置顶来源放行。"""
    raw = message("抽奖 奖品，点击参与")
    assert not detect(raw).certain
    assert detect(message("抽奖 奖品，点击参与", source="incremental_recent")).certain
    assert detect(message("抽奖 奖品，点击参与", source="pinned_today", pinned=True)).certain
    assert not detect(message("抽奖 奖品，点击参与", source="pinned", pinned=True)).certain


def test_plain_media_without_text_is_not_lottery() -> None:
    """纯媒体快照不生成抽奖发现。"""
    outcome = detect(MessageSnapshot(1, 2, "", ()))
    assert not outcome.is_lottery and outcome.reason == "no_rule_match"


@pytest.mark.asyncio
async def test_complete_rule_never_calls_ai() -> None:
    """确定规则结果不能向 AI 外发任何内容。"""
    class ForbiddenAI:
        async def analyze(self, _payload):
            raise AssertionError("不应调用 AI")

    outcome = await detect_with_ai(message("抽奖 奖品，截止时间今晚，点击参与"), ForbiddenAI())
    assert outcome.is_lottery and outcome.certain
