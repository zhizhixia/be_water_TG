from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from src.lottery_models import ActionType, LotteryAction, MessageSnapshot
from src.lottery_text_policy import is_safe_fixed_text
from src.sender import PublisherTrust


@dataclass(frozen=True)
class Plan:
    actions: tuple[LotteryAction, ...]
    manual_reason: str | None = None


DEADLINE_MARKERS = ("截止", "结束于", "截止时间", "deadline", "ends at")
CALLBACK_ACTION_ALIASES = {
    "参与", "参加", "点击参与", "点击参加", "立即参与", "点击立即参与",
    "点击参与按钮", "点击参加按钮", "点击立即参与按钮",
    "join", "clickjoin", "clickjoinbutton", "clickthejoinbutton",
}


def parse_verified_deadline(value: str | None) -> datetime | None:
    """解析带明确时区的 ISO 截止时间；含糊或无时区值均视为不可验证。"""
    if value is None or value != value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_single_callback_action(actions: tuple[str, ...]) -> bool:
    """仅接受与单一参与 callback 同义的有限动作描述。"""
    if len(actions) != 1:
        return False
    normalized = actions[0].strip().casefold().replace(" ", "")
    normalized = normalized.replace("“", "").replace("”", "").replace('"', "")
    return normalized in CALLBACK_ACTION_ALIASES


def plan(message: MessageSnapshot, text: str | None = None, *, reply: bool = False) -> Plan:
    """仅使用 sender 验证快照生成唯一、不可变的白名单动作计划。"""
    if type(message.publisher_trust) is not PublisherTrust or message.publisher_trust not in {
        PublisherTrust.OWNER,
        PublisherTrust.ADMIN,
        PublisherTrust.BOT,
    }:
        return Plan((), "untrusted_publisher")
    normalized_text = message.text.casefold()
    deadline_claimed = message.deadline_claimed or any(
        marker in normalized_text for marker in DEADLINE_MARKERS
    )
    if (deadline_claimed or message.deadline_required) and parse_verified_deadline(message.deadline) is None:
        return Plan((), "deadline_unverified")
    if message.deadline is not None and parse_verified_deadline(message.deadline) is None:
        return Plan((), "deadline_unverified")
    if message.ai_reviewed:
        if not _is_single_callback_action(message.required_actions):
            return Plan((), "unsafe_additional_condition")
    elif message.required_actions:
        return Plan((), "unsafe_additional_condition")
    forbidden_conditions = (
        "关注", "转发", "私聊", "打开链接", "点击链接", "网页", "web app",
        "投票", "点赞", "邀请", "加群", "加入", "订阅", "注册", "下载", "安装",
        "分享", "评论", "回复", "发送", "手机号", "邮箱", "验证码", "支付", "答题",
    )
    if "@" in message.text or any(token in normalized_text for token in forbidden_conditions):
        return Plan((), "unsafe_additional_condition")
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
