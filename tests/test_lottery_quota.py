from __future__ import annotations

from src.lottery_models import ParticipationStatus
from src.lottery_store import LotteryStore


def test_first_action_prewrites_and_reserves_group_and_account_quota_without_refund(tmp_path) -> None:
    """首动作必须在同一事务预写、检查冻结并占用双配额，终态不退款。"""
    store = LotteryStore(tmp_path / "state.db")
    first = store.record_entry(1, 1, "one", None, ParticipationStatus.DETECTED.value)
    second = store.record_entry(1, 2, "two", None, ParticipationStatus.DETECTED.value)
    assert store.reserve_first_action(first, "2026-08-29", 1, 1, 1, [("CLICK_BUTTON", "a")])
    store.set_entry_status(first, ParticipationStatus.PARTICIPATED)
    assert not store.reserve_first_action(second, "2026-08-29", 2, 1, 1, [("CLICK_BUTTON", "b")])


def test_result_unknown_freezes_only_its_lottery_group(tmp_path) -> None:
    """未知结果只能冻结发生该结果的群抽奖。"""
    store = LotteryStore(tmp_path / "state.db")
    entry = store.record_entry(7, 1, "fingerprint", None, ParticipationStatus.DETECTED.value)
    store.set_entry_status(entry, ParticipationStatus.RESULT_UNKNOWN)
    assert store.is_frozen(7)
    assert not store.is_frozen(8)
