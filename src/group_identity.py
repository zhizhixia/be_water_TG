from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GroupIdentity:
    """配置入口解析后的稳定 Telegram 群身份。"""

    peer_id: int
    configured_ref: str
    title: str
    username: str | None = None
    display_link: str | None = None


_PAUSE_SEVERITY = {"none": 0, "daily_limit": 1, "unanswered": 2, "safety": 3, "manual": 4}


def _latest(*values: object) -> object:
    """返回可比较时间或游标中的较新非空值。"""
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _write_migration_audit(connection: sqlite3.Connection, peer_id: int, source: str) -> None:
    """兼容旧审计表字段地记录一次稳定身份迁移。"""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(audit_event)").fetchall()}
    if {"occurred_at", "group_id", "event_type", "reason", "metadata_json"} <= columns:
        connection.execute(
            "INSERT INTO audit_event(occurred_at,group_id,event_type,reason,metadata_json) VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(), str(peer_id), "GROUP_IDENTITY_MIGRATED", "stable_peer_id", json.dumps({"source": source})),
        )
    else:
        connection.execute("INSERT INTO audit_event(group_id,event_type,reason) VALUES (?,?,?)", (str(peer_id), "GROUP_IDENTITY_MIGRATED", "stable_peer_id"))


def migrate_group_identities(connection: sqlite3.Connection, identities: list[GroupIdentity]) -> None:
    """事务化迁移 URL 状态到 peer_id，冲突时保守合并并保留审计。"""
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS group_identity (
                peer_id INTEGER PRIMARY KEY, configured_ref TEXT NOT NULL, title TEXT NOT NULL,
                username TEXT, display_link TEXT);
            CREATE TABLE IF NOT EXISTS group_identity_alias (
                configured_ref TEXT PRIMARY KEY, peer_id INTEGER NOT NULL);
        """)
        for identity in identities:
            target = str(identity.peer_id)
            source = identity.configured_ref
            connection.execute("INSERT INTO group_identity(peer_id,configured_ref,title,username,display_link) VALUES (?,?,?,?,?) ON CONFLICT(peer_id) DO UPDATE SET configured_ref=excluded.configured_ref,title=excluded.title,username=excluded.username,display_link=excluded.display_link", (identity.peer_id, source, identity.title, identity.username, identity.display_link))
            connection.execute("INSERT INTO group_identity_alias(configured_ref,peer_id) VALUES (?,?) ON CONFLICT(configured_ref) DO UPDATE SET peer_id=excluded.peer_id", (source, identity.peer_id))
            old = connection.execute("SELECT * FROM group_state WHERE group_id=?", (source,)).fetchone()
            current = connection.execute("SELECT * FROM group_state WHERE group_id=?", (target,)).fetchone()
            if old is None:
                continue
            if current is None:
                connection.execute("UPDATE group_state SET group_id=? WHERE group_id=?", (target, source))
            else:
                old_pause = str(old["pause_kind"])
                current_pause = str(current["pause_kind"])
                winner = old if _PAUSE_SEVERITY.get(old_pause, 99) > _PAUSE_SEVERITY.get(current_pause, 99) else current
                connection.execute("""
                    UPDATE group_state SET sent_count=?, last_activity_at=?, last_message_id=?,
                    consecutive_unanswered=?, pending_reservation_at=?, pause_kind=?, pause_reason=?, updated_at=?
                    WHERE group_id=?
                """, (max(old["sent_count"], current["sent_count"]), _latest(old["last_activity_at"], current["last_activity_at"]), _latest(old["last_message_id"], current["last_message_id"]), max(old["consecutive_unanswered"], current["consecutive_unanswered"]), (old if (old["updated_at"] or "") > (current["updated_at"] or "") else current)["pending_reservation_at"], winner["pause_kind"], winner["pause_reason"], _latest(old["updated_at"], current["updated_at"]) or "", target))
                connection.execute("DELETE FROM group_state WHERE group_id=?", (source,))
            _write_migration_audit(connection, identity.peer_id, source)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
