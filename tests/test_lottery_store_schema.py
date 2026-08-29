from __future__ import annotations

import sqlite3

from src.lottery_models import AlertStatus, ParticipationStatus
from src.lottery_store import LotteryStore


def test_migrate_upgrades_existing_database_idempotently_without_dropping_legacy_tables(tmp_path) -> None:
    """原地升级必须保留既有群状态和审计记录，重复执行无副作用。"""
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE group_state (group_id TEXT PRIMARY KEY, count INTEGER)")
        db.execute("INSERT INTO group_state VALUES ('legacy', 3)")
        db.execute("CREATE TABLE audit_event (id INTEGER PRIMARY KEY, event TEXT)")
        db.execute("INSERT INTO audit_event(event) VALUES ('kept')")
    LotteryStore(path).migrate()
    LotteryStore(path).migrate()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count FROM group_state WHERE group_id='legacy'").fetchone() == (3,)
        assert db.execute("SELECT event FROM audit_event").fetchone() == ("kept",)
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] >= 1


def test_entry_message_idempotency_and_alert_status_do_not_overwrite_participation(tmp_path) -> None:
    """同一消息重复写入复用记录，打开提醒不能改变参与状态。"""
    store = LotteryStore(tmp_path / "state.db")
    entry_id = store.record_entry(1, 2, "fingerprint", None, ParticipationStatus.RESULT_UNKNOWN.value, "摘要")
    assert store.record_entry(1, 2, "changed", None, ParticipationStatus.DETECTED.value) == entry_id
    opened = store.open_alert(entry_id)
    assert opened is not None
    assert opened.participation_status is ParticipationStatus.RESULT_UNKNOWN
    assert opened.alert_status is AlertStatus.OPENED
