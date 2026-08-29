from __future__ import annotations

import sqlite3

import pytest

from src.lottery_models import ParticipationStatus
from src.lottery_store import LotteryStore


def test_daily_task_completion_is_committed(tmp_path) -> None:
    """每日任务完成状态必须跨连接持久化。"""
    store = LotteryStore(tmp_path / "state.db")
    assert store.claim_daily_task(1, "2026-08-29", "CHECKIN")
    store.finish_daily_task(1, "2026-08-29", "CHECKIN", "COMPLETED")
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT status FROM group_daily_task").fetchone() == ("COMPLETED",)


def test_cleanup_before_shanghai_date_removes_all_related_lottery_records_atomically(tmp_path) -> None:
    """上海 cutoff 前的抽奖及动作、审计、消息键和内容指纹须在同一事务删除。"""
    store = LotteryStore(tmp_path / "state.db")
    old_id = store.record_entry(1, 1, "old-fingerprint", "2026-08-01T23:59:59+08:00", ParticipationStatus.DETECTED.value)
    retained_id = store.record_entry(1, 2, "new-fingerprint", "2026-08-02T00:00:00+08:00", ParticipationStatus.DETECTED.value)
    assert store.prewrite_actions(old_id, [("CLICK_BUTTON", "old")])
    assert store.prewrite_actions(retained_id, [("CLICK_BUTTON", "new")])
    store.record_lottery_audit(old_id, "DETECTED")
    store.record_lottery_audit(retained_id, "DETECTED")

    deleted = store.cleanup_before("2026-08-02")

    assert deleted == {"lottery_action": 1, "lottery_audit_event": 1, "lottery_content_fingerprint": 1, "lottery_entry": 1, "lottery_message_key": 1}
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT COUNT(*) FROM lottery_entry WHERE id=?", (old_id,)).fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM lottery_entry WHERE id=?", (retained_id,)).fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM lottery_message_key WHERE lottery_id=?", (old_id,)).fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM lottery_content_fingerprint WHERE lottery_id=?", (old_id,)).fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM lottery_audit_event WHERE lottery_id=?", (old_id,)).fetchone() == (0,)


def test_cleanup_failure_rolls_back_every_related_delete(monkeypatch, tmp_path) -> None:
    """清理任一步骤失败时不得留下部分删除。"""
    store = LotteryStore(tmp_path / "state.db")
    entry_id = store.record_entry(1, 1, "fingerprint", "2026-08-01T00:00:00+08:00", ParticipationStatus.DETECTED.value)
    assert store.prewrite_actions(entry_id, [("CLICK_BUTTON", "signature")])
    store.record_lottery_audit(entry_id, "DETECTED")
    monkeypatch.setattr(store, "_delete_cleanup_audit", lambda db, ids: (_ for _ in ()).throw(sqlite3.DatabaseError("boom")))

    with pytest.raises(sqlite3.DatabaseError, match="boom"):
        store.cleanup_before("2026-08-02")

    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT COUNT(*) FROM lottery_entry").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM lottery_action").fetchone() == (1,)
