from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.lottery_models import ActionType, LotteryAction, MessageSnapshot
from src.lottery_text_policy import is_safe_fixed_text
from src.sender import PublisherTrust


@dataclass(frozen=True)
class Plan:
    actions: tuple[LotteryAction, ...]
    manual_reason: str | None = None


def plan(message: MessageSnapshot, text: str | None = None, *, reply: bool = False) -> Plan:
    """仅使用 sender 验证快照生成唯一、不可变的白名单动作计划。"""
    if type(message.publisher_trust) is not PublisherTrust or message.publisher_trust not in {
        PublisherTrust.OWNER,
        PublisherTrust.ADMIN,
        PublisherTrust.BOT,
    }:
        return Plan((), "untrusted_publisher")
    all_buttons = [button for row in message.buttons for button in row]
    if any(button.kind != "callback" for button in all_buttons):
        return Plan((), "unsafe_button")
    candidates = [
        (row, column, button)
        for row, buttons in enumerate(message.buttons)
        for column, button in enumerate(buttons)
        if button.text.strip() in {"参与", "参加", "立即参与", "Join"}
    ]
    if len(candidates) != 1:
        return Plan((), "ambiguous_or_missing_callback")
    row, column, _ = candidates[0]
    source = f"{message.peer_id}:{message.message_id}:{message.text}:" + ";".join(
        f"{button.text}:{button.kind}:{hashlib.sha256(button.callback_data or b'').hexdigest()}"
        for button in all_buttons
    )
    signature = hashlib.sha256(source.encode("utf-8")).hexdigest()
    actions = [LotteryAction(ActionType.CLICK_BUTTON, signature, row, column)]
    if text is not None:
        if not is_safe_fixed_text(text):
            return Plan((), "unsafe_text")
        actions.append(LotteryAction(ActionType.REPLY_TEXT if reply else ActionType.SEND_TEXT, signature, text=text))
    return Plan(tuple(actions))
