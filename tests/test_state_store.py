from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.state_store import PauseKind, StateStore


TZ = ZoneInfo("Asia/Shanghai")


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 28, 12, 0, tzinfo=TZ)

    def __call__(self) -> datetime:
        return self.value


def test_state_survives_reopen(tmp_path) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path)
    assert store.increment_sent("group-a", 30).sent_count == 1

    reopened = StateStore(path)
    assert reopened.get_group_state("group-a").sent_count == 1


def test_daily_limit_pauses_group(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    state = store.increment_sent("group-a", 1)
    assert state.sent_count == 1
    assert state.pause_kind is PauseKind.DAILY_LIMIT
    with pytest.raises(RuntimeError, match="paused|limit"):
        store.increment_sent("group-a", 1)


def test_date_change_resets_auto_pause_but_not_safety(tmp_path) -> None:
    clock = Clock()
    store = StateStore(tmp_path / "state.db", now=clock)
    store.increment_sent("quota", 1)
    store.pause_group("safety", PauseKind.SAFETY, "admin warning")

    clock.value = datetime(2026, 7, 29, 1, 0, tzinfo=TZ)
    quota = store.get_group_state("quota")
    safety = store.get_group_state("safety")
    assert quota.sent_count == 0
    assert quota.pause_kind is PauseKind.NONE
    assert safety.pause_kind is PauseKind.SAFETY


def test_concurrent_increment_is_atomic(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.increment_sent("group-a", 100), range(20)))
    assert store.get_group_state("group-a").sent_count == 20


def test_unanswered_threshold_and_manual_resume(tmp_path) -> None:
    clock = Clock()
    store = StateStore(tmp_path / "state.db", now=clock)
    store.increment_sent("group-a", 30)
    clock.value += timedelta(minutes=10)
    assert not store.mark_unanswered("group-a", window_minutes=10).paused
    clock.value += timedelta(minutes=10)
    assert store.mark_unanswered("group-a", window_minutes=10).pause_kind is PauseKind.UNANSWERED
    assert store.resume_group("group-a").pause_kind is PauseKind.NONE


def test_send_reservation_is_counted_before_confirmation_and_released_on_failure(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    reserved = store.reserve_send("group-a", 2)
    assert reserved.sent_count == 1
    assert reserved.pending_reservation_at
    released = store.release_send_reservation("group-a")
    assert released.sent_count == 0
    assert released.pending_reservation_at is None

    store.reserve_send("group-a", 2)
    confirmed = store.confirm_send("group-a", 2)
    assert confirmed.sent_count == 1
    assert confirmed.last_outbound_at
    assert confirmed.pending_reservation_at is None


def test_restart_reconciles_pending_reservation_without_refunding_quota(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path)
    reserved = first.reserve_send("group-a", 3)
    assert reserved.sent_count == 1
    assert reserved.pending_reservation_at

    restarted = StateStore(path)
    recovered = restarted.reconcile_pending_reservations(["group-a"], daily_limit=3)

    assert recovered[0].sent_count == 1
    assert recovered[0].pending_reservation_at is None
    assert restarted.reserve_send("group-a", 3).sent_count == 2
    assert restarted.list_audit()[0].event_type == "reservation_recovered"


def test_mark_unanswered_waits_for_each_complete_quiet_window(tmp_path) -> None:
    current = [datetime(2026, 7, 28, 12, 0, tzinfo=TZ)]
    store = StateStore(tmp_path / "state.db", now=lambda: current[0])
    store.increment_sent("group-a", 30)

    current[0] += timedelta(minutes=9)
    assert store.mark_unanswered("group-a", window_minutes=10).consecutive_unanswered == 0

    current[0] += timedelta(minutes=1)
    assert store.mark_unanswered("group-a", window_minutes=10).consecutive_unanswered == 1

    current[0] += timedelta(minutes=10)
    state = store.mark_unanswered("group-a", window_minutes=10)
    assert state.consecutive_unanswered == 2
    assert state.pause_kind is PauseKind.UNANSWERED


def test_audit_round_trip(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.record_audit("group-a", "decision", "probability", {"allowed": False})
    event = store.list_audit()[0]
    assert event.group == "group-a"
    assert event.metadata == {"allowed": False}


def test_identity_migration_uses_peer_id_and_preserves_most_restrictive_state(tmp_path) -> None:
    """URL 与 peer 状态合并后，所有字段和审计都以稳定 peer_id 保守延续。"""
    store = StateStore(tmp_path / "state.db")
    old_ref, peer = "https://t.me/allowed", 101
    store.pause_group(old_ref, PauseKind.SAFETY, "管理员警告")
    store.touch_activity(old_ref, datetime(2026, 7, 28, 12, 0, tzinfo=TZ), 9)
    store.get_group_state(str(peer))
    store.touch_activity(str(peer), datetime(2026, 7, 28, 13, 0, tzinfo=TZ), 12)

    store.migrate_group_identities([SimpleNamespace(
        peer_id=peer, configured_ref=old_ref, title="允许群", username="allowed", display_link="https://t.me/allowed",
    )])

    state = store.get_group_state(str(peer))
    assert state.pause_kind is PauseKind.SAFETY
    assert state.pause_reason == "管理员警告"
    assert state.last_message_id == 12
    assert all(item.group != old_ref for item in store.list_group_states())
    assert any(item.event_type == "GROUP_IDENTITY_MIGRATED" and item.group == str(peer) for item in store.list_audit())
