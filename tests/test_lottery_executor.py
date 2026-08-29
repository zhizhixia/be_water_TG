import pytest
from src.lottery_executor import LotteryExecutor
from src.lottery_models import ActionType, LotteryAction, MessageSnapshot

class Store:
    def __init__(self, reserve=True): self.reserve = reserve; self.statuses = []; self.reserve_calls = []
    def reserve_first_action(self, *args):
        self.reserve_calls.append(args)
        return self.reserve
    def set_action_status(self, *args): self.statuses.append(args)
    def set_entry_status(self, *args): self.statuses.append(args)
class Sender:
    def __init__(self, outcome="ok"): self.calls=[]; self.outcome=outcome
    async def click_callback(self, *args):
        self.calls.append(args)
        if self.outcome == "timeout": raise TimeoutError()
        return self.outcome

@pytest.mark.asyncio
async def test_prewrite_or_quota_failure_never_calls_sender():
    msg = MessageSnapshot(1, 1, "x")
    sender = Sender(); executor = LotteryExecutor(sender, Store(False), lambda: "2026-08-29", 1, 3)
    assert await executor.execute(msg, (LotteryAction(ActionType.CLICK_BUTTON, "sig", 0, 0),), lambda: reread(msg), lottery_id=7) == "PERSISTENCE_FAILED"
    assert sender.calls == []

@pytest.mark.asyncio
async def test_deleted_or_changed_message_is_manual_and_never_actioned():
    msg = MessageSnapshot(1, 1, "x")
    sender = Sender(); executor = LotteryExecutor(sender, Store(), lambda: "2026-08-29", 1, 3)
    assert await executor.execute(msg, (LotteryAction(ActionType.CLICK_BUTTON, "sig", 0, 0),), lambda: reread(None), lottery_id=7) == "MANUAL_REQUIRED"
    assert sender.calls == []

@pytest.mark.asyncio
async def test_timeout_is_result_unknown_and_is_not_retried():
    msg = MessageSnapshot(1, 1, "x")
    sender = Sender("timeout"); executor = LotteryExecutor(sender, Store(), lambda: "2026-08-29", 1, 3)
    assert await executor.execute(msg, (LotteryAction(ActionType.CLICK_BUTTON, "sig", 0, 0),), lambda: reread(msg), lottery_id=7) == "RESULT_UNKNOWN"
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_passes_injected_group_and_account_limits_to_first_action_reservation():
    msg = MessageSnapshot(42, 9, "抽奖")
    store = Store()
    executor = LotteryExecutor(Sender(), store, lambda: "2026-08-29", per_group_limit=4, account_limit=12)
    actions = (LotteryAction(ActionType.CLICK_BUTTON, "sig", 0, 0),)

    assert await executor.execute(msg, actions, lambda: reread(msg), lottery_id=7) == "PARTICIPATED"
    assert store.reserve_calls == [(7, "2026-08-29", 42, 4, 12, [("CLICK_BUTTON", "sig")])]


async def reread(value): return value
