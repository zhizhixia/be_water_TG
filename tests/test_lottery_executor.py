from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.lottery_executor import LotteryExecutor
from src.lottery_models import ButtonSnapshot, MessageSnapshot
from src.lottery_planner import plan
from src.sender import PublisherTrust


class Store:
    def __init__(self, reserve=True):
        self.reserve = reserve
        self.statuses = []
        self.reserve_calls = []

    def reserve_first_action(self, *args):
        self.reserve_calls.append(args)
        return self.reserve

    def set_action_status(self, *args):
        self.statuses.append(args)

    def set_entry_status(self, *args):
        self.statuses.append(args)


class Sender:
    def __init__(self, outcome="ok"):
        self.calls = []
        self.outcome = outcome

    async def click_callback(self, *args):
        self.calls.append(args)
        if self.outcome == "timeout":
            raise TimeoutError()
        return self.outcome


def safe_message(**changes):
    """构造满足自动执行白名单的消息。"""
    values = {
        "peer_id": 1,
        "message_id": 1,
        "text": "抽奖",
        "buttons": ((ButtonSnapshot("参与", "callback", b"go"),),),
        "publisher_trust": PublisherTrust.ADMIN,
        "sender_id": 9,
    }
    values.update(changes)
    return MessageSnapshot(**values)


@pytest.mark.asyncio
async def test_prewrite_or_quota_failure_never_calls_sender():
    msg = safe_message()
    sender = Sender()
    executor = LotteryExecutor(sender, Store(False), lambda: "2026-08-29", 1, 3)
    assert await executor.execute(msg, plan(msg).actions, lambda: reread(msg), lottery_id=7) == "PERSISTENCE_FAILED"
    assert sender.calls == []


@pytest.mark.asyncio
async def test_deleted_or_changed_message_is_manual_and_never_actioned():
    msg = safe_message()
    sender = Sender()
    executor = LotteryExecutor(sender, Store(), lambda: "2026-08-29", 1, 3)
    assert await executor.execute(msg, plan(msg).actions, lambda: reread(None), lottery_id=7) == "MANUAL_REQUIRED"
    assert sender.calls == []


@pytest.mark.asyncio
async def test_timeout_is_result_unknown_and_is_not_retried():
    msg = safe_message()
    sender = Sender("timeout")
    executor = LotteryExecutor(sender, Store(), lambda: "2026-08-29", 1, 3)
    assert await executor.execute(msg, plan(msg).actions, lambda: reread(msg), lottery_id=7) == "RESULT_UNKNOWN"
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_passes_injected_group_and_account_limits_to_first_action_reservation():
    msg = safe_message(peer_id=42, message_id=9)
    store = Store()
    executor = LotteryExecutor(Sender(), store, lambda: "2026-08-29", per_group_limit=4, account_limit=12)
    actions = plan(msg).actions

    assert await executor.execute(msg, actions, lambda: reread(msg), lottery_id=7) == "PARTICIPATED"
    assert store.reserve_calls == [(7, "2026-08-29", 42, 4, 12, [("CLICK_BUTTON", actions[0].source_signature)])]


@pytest.mark.asyncio
async def test_execution_rereads_trust_deadline_and_full_plan_before_reserving():
    """发布者降权、截止和新增非白名单条件都必须在配额预留前拒绝。"""
    sender = Sender()
    now = lambda: datetime(2026, 8, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    cases = [
        (safe_message(), PublisherTrust.UNKNOWN, "MANUAL_REQUIRED"),
        (
            safe_message(
                text="抽奖 截止 2026-08-29T11:00:00+08:00",
                deadline="2026-08-29T11:00:00+08:00",
                deadline_claimed=True,
            ),
            PublisherTrust.ADMIN,
            "EXPIRED",
        ),
        (safe_message(text="抽奖 截止 还需转发"), PublisherTrust.ADMIN, "MANUAL_REQUIRED"),
    ]
    for message, trust, expected in cases:
        store = Store()
        executor = LotteryExecutor(sender, store, lambda: "2026-08-29")
        result = await executor.execute(
            message,
            plan(message).actions,
            lambda value=message: reread(value),
            lottery_id=7,
            trust_resolver=lambda *_args, value=trust: reread(value),
            now=now,
        )
        assert result == expected
        assert store.reserve_calls == []


@pytest.mark.asyncio
async def test_pause_gate_is_checked_again_immediately_before_external_action():
    """复核期间进入暂停时可预写但绝不触发 Telegram。"""
    message = safe_message()
    allowed = [True]

    async def trust(*_args):
        allowed[0] = False
        return PublisherTrust.ADMIN

    sender = Sender()
    store = Store()
    executor = LotteryExecutor(sender, store, lambda: "2026-08-29")
    result = await executor.execute(
        message,
        plan(message).actions,
        lambda: reread(message),
        lottery_id=7,
        trust_resolver=trust,
        can_execute=lambda: allowed[0],
    )
    assert result == "CANCELLED"
    assert sender.calls == []
    assert store.reserve_calls == []


async def reread(value):
    return value
