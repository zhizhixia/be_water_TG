from __future__ import annotations

from dataclasses import dataclass

from src.lottery_ai import LotteryAI, analyze
from src.lottery_models import MessageSnapshot


@dataclass(frozen=True)
class Detection:
    is_lottery: bool
    certain: bool
    reason: str


def detect(message: MessageSnapshot, *, allowed_without_deadline: bool = False) -> Detection:
    """规则优先识别；不确定结果交给人工或受限 AI。"""
    text = message.text.casefold()
    if any(word in text for word in ("抽奖结束", "开奖结束", "已结束")):
        return Detection(False, True, "ended")
    lottery = any(word in text for word in ("抽奖", "开奖", "赠送", "奖品", "giveaway"))
    deadline = any(word in text for word in ("截止", "结束于", "截止时间"))
    buttons = bool(message.buttons)
    no_deadline_is_safe = allowed_without_deadline or message.source == "incremental_recent" or (message.source == "pinned_today" and message.pinned)
    if lottery and (deadline or no_deadline_is_safe) and (buttons or "参与" in text):
        return Detection(True, True, "rule_match")
    return Detection(False, False, "uncertain" if lottery else "no_rule_match")


async def detect_with_ai(message: MessageSnapshot, ai: LotteryAI | None) -> Detection:
    """规则优先识别；仅将不确定文本快照交给受限 schema AI。"""
    rule_result = detect(message)
    if rule_result.certain or ai is None:
        return rule_result
    result = await analyze(
        ai,
        {
            "text": message.text,
            "context": {"source": message.source, "occurred_at": message.occurred_at},
            "buttons": [
                {"text": button.text, "kind": button.kind}
                for row in message.buttons
                for button in row
            ],
        },
    )
    if result is None:
        return rule_result
    return Detection(result.is_lottery, True, "ai_match" if result.is_lottery else "ai_not_lottery")
