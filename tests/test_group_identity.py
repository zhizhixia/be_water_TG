from __future__ import annotations

import sqlite3

import pytest

from src.group_identity import GroupIdentity, migrate_group_identities


def setup_legacy_db(path) -> sqlite3.Connection:
    """建立最小旧群状态表用于迁移验证。"""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE group_state (
            group_id TEXT PRIMARY KEY, sent_count INTEGER, last_activity_at TEXT,
            last_message_id INTEGER, consecutive_unanswered INTEGER,
            pending_reservation_at TEXT, pause_kind TEXT, pause_reason TEXT, updated_at TEXT);
        CREATE TABLE audit_event (id INTEGER PRIMARY KEY, group_id TEXT, event_type TEXT, reason TEXT);
    """)
    return db


def test_migration_conservatively_merges_url_state_into_stable_peer_id(tmp_path) -> None:
    """冲突时计数、时间、游标、未回复和预留保守合并，并保留严重暂停与审计。"""
    db = setup_legacy_db(tmp_path / "state.db")
    db.execute("INSERT INTO group_state VALUES ('https://t.me/old',2,'2026-08-01',4,1,'old','daily_limit','old reason','2026-08-01')")
    db.execute("INSERT INTO group_state VALUES ('99',5,'2026-08-02',3,4,'new','manual','manual reason','2026-08-02')")
    db.commit()
    migrate_group_identities(db, [GroupIdentity(99, "https://t.me/old", "群")])
    row = db.execute("SELECT * FROM group_state WHERE group_id='99'").fetchone()
    assert row["sent_count"] == 5
    assert row["last_activity_at"] == "2026-08-02"
    assert row["last_message_id"] == 4
    assert row["consecutive_unanswered"] == 4
    assert row["pending_reservation_at"] == "new"
    assert row["pause_kind"] == "manual"
    assert db.execute("SELECT COUNT(*) FROM group_state WHERE group_id='https://t.me/old'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_event WHERE group_id='99'").fetchone()[0] == 1


def test_identity_migration_rolls_back_everything_on_failure(monkeypatch, tmp_path) -> None:
    """迁移中的审计写入失败必须完整回滚。"""
    db = setup_legacy_db(tmp_path / "state.db")
    db.execute("INSERT INTO group_state VALUES ('old',1,NULL,1,0,NULL,'none','','')")
    db.commit()
    monkeypatch.setattr("src.group_identity._write_migration_audit", lambda *args: (_ for _ in ()).throw(sqlite3.DatabaseError("boom")))
    with pytest.raises(sqlite3.DatabaseError, match="boom"):
        migrate_group_identities(db, [GroupIdentity(9, "old", "群")])
    assert db.execute("SELECT group_id FROM group_state").fetchone()[0] == "old"
