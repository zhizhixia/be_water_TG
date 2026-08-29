from __future__ import annotations

from src.lottery_models import ActionType, ButtonSnapshot, MessageSnapshot
from src.lottery_planner import plan
from src.sender import PublisherTrust


def trusted_message(buttons) -> MessageSnapshot:
    """构造已由 sender 当前实体认证的消息快照。"""
    return MessageSnapshot(1, 7, "抽奖 截止", buttons, publisher_trust=PublisherTrust.ADMIN)


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
