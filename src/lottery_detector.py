from __future__ import annotations

import re
from dataclasses import dataclass

from src.lottery_ai import LotteryAI, analyze
from src.lottery_models import MessageSnapshot


@dataclass(frozen=True)
class Detection:
    is_lottery: bool
    certain: bool
    reason: str
    deadline: str | None = None
    deadline_claimed: bool = False
    deadline_required: bool = False
    required_actions: tuple[str, ...] = ()
    ai_reviewed: bool = False


DEADLINE_MARKERS = ("截止", "结束于", "截止时间", "deadline", "ends at")
ISO_DEADLINE_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b",
    re.IGNORECASE,
)


def _verified_deadline_in_text(text: str) -> str | None:
    """仅提取正文中自带时区的 ISO 截止时间，其他表达留给安全复核。"""
    match = ISO_DEADLINE_PATTERN.search(text)
    return match.group(0) if match else None


def detect(message: MessageSnapshot, *, allowed_without_deadline: bool = False) -> Detection:
    """规则优先识别；不确定结果交给人工或受限 AI。"""
    text = message.text.casefold()
    if any(word in text for word in ("抽奖结束", "开奖结束", "已结束")):
        return Detection(False, True, "ended")
    lottery = any(word in text for word in ("抽奖", "开奖", "赠送", "奖品", "giveaway"))
    deadline_claimed = any(word in text for word in DEADLINE_MARKERS)
    deadline = _verified_deadline_in_text(message.text) if deadline_claimed else None
    buttons = bool(message.buttons)
    no_deadline_is_safe = allowed_without_deadline or message.source == "incremental_recent" or (message.source == "pinned_today" and message.pinned)
    if lottery and (deadline_claimed or no_deadline_is_safe) and (buttons or "参与" in text):
        if deadline_claimed and deadline is None:
            return Detection(
                True,
                False,
                "deadline_unverified",
                deadline_claimed=True,
                deadline_required=True,
            )
        return Detection(
            True,
            True,
            "rule_match",
            deadline=deadline,
            deadline_claimed=deadline_claimed,
            deadline_required=deadline_claimed,
        )
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
    return Detection(
        result.is_lottery,
        True,
        "ai_match" if result.is_lottery else "ai_not_lottery",
        deadline=result.deadline,
        deadline_claimed=rule_result.deadline_claimed or result.deadline is not None,
        deadline_required=not (
            message.source == "incremental_recent"
            or (message.source == "pinned_today" and message.pinned)
        ),
        required_actions=result.required_actions,
        ai_reviewed=True,
    )
