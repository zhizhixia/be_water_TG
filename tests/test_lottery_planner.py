from __future__ import annotations

from src.lottery_models import ActionType, ButtonSnapshot, MessageSnapshot
from src.lottery_planner import plan
from src.sender import PublisherTrust


def trusted_message(buttons) -> MessageSnapshot:
    """构造已由 sender 当前实体认证的消息快照。"""
    return MessageSnapshot(1, 7, "抽奖", buttons, publisher_trust=PublisherTrust.ADMIN)


def test_planner_only_accepts_sender_trust_enum_and_unique_callback() -> None:
    """伪造字符串发布者或多个候选回调必须转人工。"""
    callback = ButtonSnapshot("立即参与", "callback", b"join")
    safe = trusted_message(((callback, ButtonSnapshot("规则", "callback", b"rules")),))
    result = plan(safe)
    assert len(result.actions) == 1 and result.actions[0].kind is ActionType.CLICK_BUTTON

    forged = MessageSnapshot(1, 7, "抽奖", ((callback,),), publisher_trust="admin")
    assert plan(forged).manual_reason == "untrusted_publisher"
    assert plan(trusted_message(((callback, ButtonSnapshot("参加", "callback", b"other")),))).manual_reason == "ambiguous_or_missing_callback"


def test_planner_rejects_any_external_button_and_builds_reply_fixed_text() -> None:
    """任何 URL/WebApp 即使可选也整体人工；固定文本可明确为回复。"""
    callback = ButtonSnapshot("参与", "callback", b"join")
    unsafe = trusted_message(((callback, ButtonSnapshot("加码", "url")),))
    assert plan(unsafe).manual_reason == "unsafe_button"

    result = plan(trusted_message(((callback,),)), text="+1", reply=True)
    assert [action.kind for action in result.actions] == [ActionType.CLICK_BUTTON, ActionType.REPLY_TEXT]
    assert result.actions[-1].text == "+1"


def test_planner_rejects_entire_entry_with_additional_unsafe_condition() -> None:
    """唯一参与按钮也不能掩盖转发、提及等额外条件。"""
    callback = ButtonSnapshot("参与", "callback", b"join")
    message = MessageSnapshot(
        1, 7, "抽奖：参与后还需转发并 @好友",
        ((callback,),), publisher_trust=PublisherTrust.ADMIN,
    )
    assert plan(message).manual_reason == "unsafe_additional_condition"


def test_planner_only_accepts_single_ai_action_synonymous_with_callback() -> None:
    """AI 安全元数据只能复核单一 callback，同义范围外或多余条件均转人工。"""
    callback = ButtonSnapshot("参与", "callback", b"join")
    values = {
        "peer_id": 1,
        "message_id": 7,
        "text": "抽奖，截止今晚",
        "buttons": ((callback,),),
        "publisher_trust": PublisherTrust.ADMIN,
        "deadline": "2026-09-01T12:00:00+08:00",
        "deadline_claimed": True,
        "ai_reviewed": True,
    }
    safe = MessageSnapshot(**values, required_actions=("点击参与",))
    assert len(plan(safe).actions) == 1

    for actions in ((), ("关注频道",), ("点击参与", "转发")):
        message = MessageSnapshot(**values, required_actions=actions)
        assert plan(message).manual_reason == "unsafe_additional_condition"


def test_planner_rejects_claimed_or_ambiguous_deadline_without_verified_value() -> None:
    """正文声称有截止但缺值、格式含糊或无时区时一律转人工。"""
    callback = ButtonSnapshot("参与", "callback", b"join")
    for deadline in (None, "今晚", "2026-09-01T12:00:00"):
        message = MessageSnapshot(
            1, 7, "抽奖，截止今晚", ((callback,),),
            publisher_trust=PublisherTrust.ADMIN,
            deadline=deadline,
        )
        assert plan(message).manual_reason == "deadline_unverified"

    unsafe_source = MessageSnapshot(
        1, 8, "抽奖", ((callback,),),
        publisher_trust=PublisherTrust.ADMIN,
        deadline_required=True,
        required_actions=("点击参与",),
        ai_reviewed=True,
    )
    assert plan(unsafe_source).manual_reason == "deadline_unverified"
