from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.group_identity import migrate_group_identities as migrate_stable_group_identities


APP_TIMEZONE = ZoneInfo("Asia/Shanghai")


class PauseKind(str, Enum):
    NONE = "none"
    DAILY_LIMIT = "daily_limit"
    UNANSWERED = "unanswered"
    SAFETY = "safety"
    MANUAL = "manual"


@dataclass(frozen=True)
class GroupState:
    group: str
    local_date: str
    sent_count: int = 0
    last_activity_at: str | None = None
    last_outbound_at: str | None = None
    last_message_id: int | None = None
    consecutive_unanswered: int = 0
    last_unanswered_outbound_at: str | None = None
    last_unanswered_checked_at: str | None = None
    pending_reservation_at: str | None = None
    pause_kind: PauseKind = PauseKind.NONE
    pause_reason: str = ""
    updated_at: str = ""

    @property
    def paused(self) -> bool:
        return self.pause_kind is not PauseKind.NONE


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    occurred_at: str
    group: str
    event_type: str
    reason: str
    metadata: dict[str, Any]


class StateStore:
    """Thread-safe SQLite persistence for per-group participation state."""

    def __init__(
        self,
        path: str | Path,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(APP_TIMEZONE))
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        with self._schema_lock, closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS group_state (
                    group_id TEXT PRIMARY KEY,
                    local_date TEXT NOT NULL,
                    sent_count INTEGER NOT NULL DEFAULT 0,
                    last_activity_at TEXT,
                    last_outbound_at TEXT,
                    last_message_id INTEGER,
                    consecutive_unanswered INTEGER NOT NULL DEFAULT 0,
                    last_unanswered_outbound_at TEXT,
                    last_unanswered_checked_at TEXT,
                    pending_reservation_at TEXT,
                    pause_kind TEXT NOT NULL DEFAULT 'none',
                    pause_reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_event (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_audit_event_time
                    ON audit_event(occurred_at DESC);
                CREATE TABLE IF NOT EXISTS group_identity (
                    peer_id INTEGER PRIMARY KEY, configured_ref TEXT NOT NULL,
                    title TEXT NOT NULL, username TEXT, display_link TEXT
                );
                CREATE TABLE IF NOT EXISTS group_identity_alias (
                    configured_ref TEXT PRIMARY KEY, peer_id INTEGER NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(group_state)").fetchall()
            }
            if "last_unanswered_outbound_at" not in columns:
                connection.execute(
                    "ALTER TABLE group_state ADD COLUMN last_unanswered_outbound_at TEXT"
                )
            if "last_unanswered_checked_at" not in columns:
                connection.execute(
                    "ALTER TABLE group_state ADD COLUMN last_unanswered_checked_at TEXT"
                )
            if "pending_reservation_at" not in columns:
                connection.execute(
                    "ALTER TABLE group_state ADD COLUMN pending_reservation_at TEXT"
                )

    def migrate_group_identities(self, identities: list[object]) -> None:
        """事务化写入 peer_id 与别名，旧 URL 状态保守合并到稳定 ID。"""
        with closing(self._connect()) as connection:
            migrate_stable_group_identities(connection, identities)

    def _clock(self) -> tuple[datetime, str, str]:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=APP_TIMEZONE)
        value = value.astimezone(APP_TIMEZONE)
        return value, value.date().isoformat(), value.isoformat()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> GroupState:
        return GroupState(
            group=row["group_id"],
            local_date=row["local_date"],
            sent_count=row["sent_count"],
            last_activity_at=row["last_activity_at"],
            last_outbound_at=row["last_outbound_at"],
            last_message_id=row["last_message_id"],
            consecutive_unanswered=row["consecutive_unanswered"],
            last_unanswered_outbound_at=row["last_unanswered_outbound_at"],
            last_unanswered_checked_at=row["last_unanswered_checked_at"],
            pending_reservation_at=row["pending_reservation_at"],
            pause_kind=PauseKind(row["pause_kind"]),
            pause_reason=row["pause_reason"],
            updated_at=row["updated_at"],
        )

    def _reset_daily_if_needed(
        self, connection: sqlite3.Connection, group: str, today: str, now_iso: str
    ) -> None:
        row = connection.execute(
            "SELECT local_date, pause_kind, pause_reason FROM group_state WHERE group_id = ?",
            (group,),
        ).fetchone()
        if row is None or row["local_date"] == today:
            return
        pause_kind = PauseKind(row["pause_kind"])
        auto_resume = pause_kind in (PauseKind.DAILY_LIMIT, PauseKind.UNANSWERED)
        connection.execute(
            """
            UPDATE group_state
            SET local_date = ?, sent_count = 0, consecutive_unanswered = 0,
                last_unanswered_outbound_at = NULL, last_unanswered_checked_at = NULL,
                pending_reservation_at = NULL, pause_kind = ?, pause_reason = ?, updated_at = ?
            WHERE group_id = ?
            """,
            (
                today,
                PauseKind.NONE.value if auto_resume else pause_kind.value,
                "" if auto_resume else row["pause_reason"],
                now_iso,
                group,
            ),
        )

    def get_group_state(self, group: str) -> GroupState:
        _, today, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reset_daily_if_needed(connection, group, today, now_iso)
            connection.execute(
                """
                INSERT OR IGNORE INTO group_state(group_id, local_date, updated_at)
                VALUES (?, ?, ?)
                """,
                (group, today, now_iso),
            )
            row = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            connection.commit()
        assert row is not None
        return self._from_row(row)

    def list_group_states(self, groups: list[str] | None = None) -> list[GroupState]:
        if groups is not None:
            return [self.get_group_state(group) for group in groups]
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM group_state ORDER BY group_id"
            ).fetchall()
        return [self.get_group_state(row["group_id"]) for row in rows]

    def touch_activity(
        self, group: str, occurred_at: datetime | None = None, message_id: int | None = None
    ) -> GroupState:
        state = self.get_group_state(group)
        timestamp = (occurred_at or self._clock()[0])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=APP_TIMEZONE)
        timestamp_iso = timestamp.astimezone(APP_TIMEZONE).isoformat()
        _, _, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE group_state
                SET last_activity_at = ?, last_message_id = COALESCE(?, last_message_id),
                    consecutive_unanswered = CASE
                        WHEN last_outbound_at IS NOT NULL AND ? > last_outbound_at THEN 0
                        ELSE consecutive_unanswered END,
                    updated_at = ?
                WHERE group_id = ?
                """,
                (timestamp_iso, message_id, timestamp_iso, now_iso, group),
            )
        return self.get_group_state(state.group)

    def reserve_send(self, group: str, daily_limit: int) -> GroupState:
        """Durably reserve one quota slot before contacting Telegram."""
        _, today, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reset_daily_if_needed(connection, group, today, now_iso)
            connection.execute(
                """
                INSERT OR IGNORE INTO group_state(group_id, local_date, updated_at)
                VALUES (?, ?, ?)
                """,
                (group, today, now_iso),
            )
            row = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            assert row is not None
            if PauseKind(row["pause_kind"]) is not PauseKind.NONE:
                connection.rollback()
                raise RuntimeError(f"Group is paused: {row['pause_kind']}")
            if row["pending_reservation_at"]:
                connection.rollback()
                raise RuntimeError("A send reservation is already pending")
            if row["sent_count"] >= daily_limit:
                connection.execute(
                    """
                    UPDATE group_state SET pause_kind = ?, pause_reason = ?, updated_at = ?
                    WHERE group_id = ?
                    """,
                    (PauseKind.DAILY_LIMIT.value, "Daily limit reached", now_iso, group),
                )
                connection.commit()
                raise RuntimeError("Daily limit reached")
            new_count = row["sent_count"] + 1
            connection.execute(
                """
                UPDATE group_state
                SET sent_count = ?, pending_reservation_at = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (
                    new_count,
                    now_iso,
                    now_iso,
                    group,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            connection.commit()
        assert updated is not None
        return self._from_row(updated)

    def reconcile_pending_reservations(
        self,
        groups: list[str] | None,
        daily_limit: int,
    ) -> list[GroupState]:
        """Close crash-left reservations while retaining their quota charge.

        A pre-send reservation is intentionally counted conservatively because
        after a process crash Telegram delivery is unknown.  On the next send
        loop startup the in-flight marker must still be cleared, otherwise the
        group can never reserve another slot that day.
        """
        _, today, now_iso = self._clock()
        recovered: list[tuple[str, str]] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if groups is None:
                rows = connection.execute(
                    "SELECT group_id FROM group_state WHERE pending_reservation_at IS NOT NULL"
                ).fetchall()
                selected_groups = [row["group_id"] for row in rows]
            else:
                selected_groups = list(dict.fromkeys(groups))
            for group in selected_groups:
                self._reset_daily_if_needed(connection, group, today, now_iso)
                row = connection.execute(
                    "SELECT * FROM group_state WHERE group_id = ?", (group,)
                ).fetchone()
                if row is None or not row["pending_reservation_at"]:
                    continue
                pause_kind = PauseKind(row["pause_kind"])
                if pause_kind is PauseKind.NONE and row["sent_count"] >= daily_limit:
                    pause_kind = PauseKind.DAILY_LIMIT
                connection.execute(
                    """
                    UPDATE group_state
                    SET pending_reservation_at = NULL, pause_kind = ?, pause_reason = ?,
                        updated_at = ?
                    WHERE group_id = ?
                    """,
                    (
                        pause_kind.value,
                        "Daily limit reached"
                        if pause_kind is PauseKind.DAILY_LIMIT
                        else row["pause_reason"],
                        now_iso,
                        group,
                    ),
                )
                recovered.append((group, row["pending_reservation_at"]))
            connection.commit()

        for group, reserved_at in recovered:
            self.record_audit(
                group,
                "reservation_recovered",
                "Recovered unfinished reservation after restart",
                {"reserved_at": reserved_at, "quota_refunded": False},
            )
        return [self.get_group_state(group) for group, _ in recovered]

    def confirm_send(self, group: str, daily_limit: int) -> GroupState:
        """Confirm a successful send and close its durable reservation."""
        _, today, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reset_daily_if_needed(connection, group, today, now_iso)
            row = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            if row is None or not row["pending_reservation_at"]:
                connection.rollback()
                raise RuntimeError("No send reservation to confirm")
            pause_kind = (
                PauseKind.DAILY_LIMIT if row["sent_count"] >= daily_limit else PauseKind.NONE
            )
            connection.execute(
                """
                UPDATE group_state
                SET last_outbound_at = ?, pending_reservation_at = NULL,
                    pause_kind = ?, pause_reason = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (
                    now_iso,
                    pause_kind.value,
                    "Daily limit reached" if pause_kind is PauseKind.DAILY_LIMIT else "",
                    now_iso,
                    group,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            connection.commit()
        assert updated is not None
        return self._from_row(updated)

    def release_send_reservation(self, group: str) -> GroupState:
        """Release a quota slot when its send did not reach Telegram."""
        _, _, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            if row is None or not row["pending_reservation_at"]:
                connection.rollback()
                raise RuntimeError("No send reservation to release")
            connection.execute(
                """
                UPDATE group_state
                SET sent_count = MAX(sent_count - 1, 0), pending_reservation_at = NULL,
                    pause_kind = CASE WHEN pause_kind = 'daily_limit' THEN 'none' ELSE pause_kind END,
                    pause_reason = CASE WHEN pause_kind = 'daily_limit' THEN '' ELSE pause_reason END,
                    updated_at = ?
                WHERE group_id = ?
                """,
                (now_iso, group),
            )
            updated = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            connection.commit()
        assert updated is not None
        return self._from_row(updated)

    def increment_sent(self, group: str, daily_limit: int) -> GroupState:
        """Backward-compatible atomic increment for callers without I/O in between."""
        _, today, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reset_daily_if_needed(connection, group, today, now_iso)
            connection.execute(
                """
                INSERT OR IGNORE INTO group_state(group_id, local_date, updated_at)
                VALUES (?, ?, ?)
                """,
                (group, today, now_iso),
            )
            row = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            assert row is not None
            if PauseKind(row["pause_kind"]) is not PauseKind.NONE:
                connection.rollback()
                raise RuntimeError(f"Group is paused: {row['pause_kind']}")
            if row["sent_count"] >= daily_limit:
                connection.execute(
                    """
                    UPDATE group_state SET pause_kind = ?, pause_reason = ?, updated_at = ?
                    WHERE group_id = ?
                    """,
                    (PauseKind.DAILY_LIMIT.value, "Daily limit reached", now_iso, group),
                )
                connection.commit()
                raise RuntimeError("Daily limit reached")
            new_count = row["sent_count"] + 1
            pause_kind = PauseKind.DAILY_LIMIT if new_count >= daily_limit else PauseKind.NONE
            connection.execute(
                """
                UPDATE group_state
                SET sent_count = ?, last_outbound_at = ?, pause_kind = ?, pause_reason = ?,
                    updated_at = ?
                WHERE group_id = ?
                """,
                (
                    new_count,
                    now_iso,
                    pause_kind.value,
                    "Daily limit reached" if pause_kind is PauseKind.DAILY_LIMIT else "",
                    now_iso,
                    group,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM group_state WHERE group_id = ?", (group,)
            ).fetchone()
            connection.commit()
        assert updated is not None
        return self._from_row(updated)

    def mark_unanswered(
        self, group: str, threshold: int = 2, window_minutes: int = 10
    ) -> GroupState:
        """Count consecutive full quiet windows after an outbound message."""
        state = self.get_group_state(group)
        if not state.last_outbound_at:
            return state
        now, _, now_iso = self._clock()
        if state.last_activity_at and state.last_activity_at > state.last_outbound_at:
            return state
        window_started_at = datetime.fromisoformat(
            state.last_unanswered_checked_at or state.last_outbound_at
        )
        if (now - window_started_at).total_seconds() < window_minutes * 60:
            return state
        count = state.consecutive_unanswered + 1
        pause = count >= threshold
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE group_state SET consecutive_unanswered = ?,
                    last_unanswered_outbound_at = ?, last_unanswered_checked_at = ?, pause_kind = ?,
                    pause_reason = ?, updated_at = ? WHERE group_id = ?
                """,
                (
                    count,
                    state.last_outbound_at,
                    now_iso,
                    PauseKind.UNANSWERED.value if pause else state.pause_kind.value,
                    "Two consecutive messages received no response" if pause else state.pause_reason,
                    now_iso,
                    group,
                ),
            )
        return self.get_group_state(group)

    def pause_group(self, group: str, kind: PauseKind, reason: str) -> GroupState:
        self.get_group_state(group)
        _, _, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE group_state SET pause_kind = ?, pause_reason = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (kind.value, reason, now_iso, group),
            )
        self.record_audit(group, "group_paused", reason, {"pause_kind": kind.value})
        return self.get_group_state(group)

    def resume_group(self, group: str) -> GroupState:
        self.get_group_state(group)
        _, _, now_iso = self._clock()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE group_state SET pause_kind = 'none', pause_reason = '',
                    consecutive_unanswered = 0, updated_at = ? WHERE group_id = ?
                """,
                (now_iso, group),
            )
        self.record_audit(group, "group_resumed", "Manual review completed")
        return self.get_group_state(group)

    def record_audit(
        self,
        group: str,
        event_type: str,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        _, _, now_iso = self._clock()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_event(occurred_at, group_id, event_type, reason, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (now_iso, group, event_type, reason, json.dumps(metadata or {}, ensure_ascii=False)),
            )
        return int(cursor.lastrowid)

    def list_audit(self, limit: int = 100) -> list[AuditEvent]:
        safe_limit = min(max(limit, 1), 500)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM audit_event ORDER BY seq DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [
            AuditEvent(
                seq=row["seq"],
                occurred_at=row["occurred_at"],
                group=row["group_id"],
                event_type=row["event_type"],
                reason=row["reason"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    @staticmethod
    def serialize_state(state: GroupState) -> dict[str, Any]:
        data = asdict(state)
        data["pause_kind"] = state.pause_kind.value
        data["paused"] = state.paused
        return data
