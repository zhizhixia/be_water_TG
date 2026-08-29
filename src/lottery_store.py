from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from src.lottery_models import AlertStatus, LotteryEntry, ParticipationStatus


class LotteryStore:
    """抽奖 SQLite 仓储；所有状态变更均使用独占写事务并在提交后返回。"""

    SCHEMA_VERSION = 2

    def __init__(self, path: str | Path) -> None:
        """打开数据库并执行可重复的原地 schema 升级。"""
        self.path = str(Path(path))
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        """创建启用 WAL 的独立 SQLite 连接。"""
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _write(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        """在 BEGIN IMMEDIATE 事务内运行写入，异常时完整回滚。"""
        with closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                result = operation(db)
                db.commit()
                return result
            except Exception:
                db.rollback()
                raise

    def migrate(self) -> None:
        """原地、幂等升级 schema；升级错误向上抛出以 fail closed。"""
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        def operation(db: sqlite3.Connection) -> None:
            db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            db.execute("INSERT INTO schema_version(version) SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM schema_version)")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS lottery_entry (
                    id INTEGER PRIMARY KEY, peer_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                    base_fingerprint TEXT NOT NULL, deadline TEXT, participation_status TEXT NOT NULL,
                    alert_status TEXT NOT NULL DEFAULT 'NONE', summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '', UNIQUE(peer_id, message_id));
                CREATE INDEX IF NOT EXISTS idx_lottery_fingerprint ON lottery_entry(base_fingerprint, deadline);
                CREATE TABLE IF NOT EXISTS lottery_action (
                    id INTEGER PRIMARY KEY, lottery_id INTEGER NOT NULL, step INTEGER NOT NULL,
                    action_type TEXT NOT NULL, source_signature TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, UNIQUE(lottery_id, step));
                CREATE TABLE IF NOT EXISTS lottery_daily_quota (
                    local_date TEXT NOT NULL, peer_id INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(local_date, peer_id));
                CREATE TABLE IF NOT EXISTS lottery_group_freeze (
                    peer_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, lottery_id INTEGER);
                CREATE TABLE IF NOT EXISTS lottery_scan_state (
                    peer_id INTEGER PRIMARY KEY, cursor_message_id INTEGER, last_attempt_at TEXT,
                    last_success_at TEXT, last_error TEXT);
                CREATE TABLE IF NOT EXISTS daily_batch (local_date TEXT PRIMARY KEY, groups_json TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS group_daily_task (
                    peer_id INTEGER NOT NULL, local_date TEXT NOT NULL, task_type TEXT NOT NULL,
                    status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(peer_id, local_date, task_type));
                CREATE TABLE IF NOT EXISTS lottery_message_key (
                    peer_id INTEGER NOT NULL, message_id INTEGER NOT NULL, lottery_id INTEGER NOT NULL,
                    PRIMARY KEY(peer_id, message_id));
                CREATE TABLE IF NOT EXISTS lottery_content_fingerprint (
                    lottery_id INTEGER PRIMARY KEY, base_fingerprint TEXT NOT NULL, deadline TEXT);
                CREATE TABLE IF NOT EXISTS lottery_audit_event (
                    id INTEGER PRIMARY KEY, lottery_id INTEGER NOT NULL, event TEXT NOT NULL);
            """)
            db.execute("UPDATE schema_version SET version=?", (self.SCHEMA_VERSION,))
        self._write(operation)

    def scan_state(self, peer_id: int) -> dict[str, int | str | None]:
        """读取群扫描游标；未初始化群返回不推进游标的安全状态。"""
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT peer_id, cursor_message_id, last_attempt_at, last_success_at, last_error "
                "FROM lottery_scan_state WHERE peer_id=?",
                (peer_id,),
            ).fetchone()
        if row is None:
            return {
                "peer_id": peer_id,
                "cursor_message_id": None,
                "last_attempt_at": None,
                "last_success_at": None,
                "last_error": None,
            }
        return dict(row)

    def commit_scan_batch(
        self, peer_id: int, cursor_message_id: int, messages: list[Any] | None = None
    ) -> None:
        """原子提交单群扫描游标，且绝不持久化消息媒体或回调对象。"""
        _ = messages

        def operation(db: sqlite3.Connection) -> None:
            db.execute(
                """
                INSERT INTO lottery_scan_state(peer_id, cursor_message_id)
                VALUES (?, ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                    cursor_message_id = CASE
                        WHEN lottery_scan_state.cursor_message_id IS NULL
                          OR excluded.cursor_message_id > lottery_scan_state.cursor_message_id
                        THEN excluded.cursor_message_id
                        ELSE lottery_scan_state.cursor_message_id
                    END
                """,
                (peer_id, cursor_message_id),
            )

        self._write(operation)

    def create_daily_batch(self, local_date: str, peer_ids: list[int]) -> bool:
        """幂等保存当天群快照。"""
        return self._write(lambda db: db.execute("INSERT OR IGNORE INTO daily_batch(local_date,groups_json,status) VALUES (?,?,?)", (local_date, json.dumps(peer_ids), "PENDING")).rowcount == 1)

    def daily_batch_groups(self, local_date: str) -> list[int]:
        """读取已冻结的当天群快照。"""
        with closing(self._connect()) as db:
            row = db.execute("SELECT groups_json FROM daily_batch WHERE local_date=?", (local_date,)).fetchone()
        return [int(value) for value in json.loads(row["groups_json"])] if row else []

    def claim_daily_task(self, peer_id: int, local_date: str, task_type: str) -> bool:
        """原子领取每群每日单次任务。"""
        return self._write(lambda db: db.execute("INSERT OR IGNORE INTO group_daily_task(peer_id,local_date,task_type,status,attempt_count) VALUES (?,?,?,?,1)", (peer_id, local_date, task_type, "EXECUTING")).rowcount == 1)

    def finish_daily_task(self, peer_id: int, local_date: str, task_type: str, status: str) -> None:
        """提交每日任务终态。"""
        self._write(lambda db: db.execute("UPDATE group_daily_task SET status=? WHERE peer_id=? AND local_date=? AND task_type=?", (status, peer_id, local_date, task_type)))

    def record_entry(self, peer_id: int, message_id: int, fingerprint: str, deadline: str | None, status: str, summary: str = "") -> int:
        """原子记录发现；同一 Telegram 消息始终返回既有记录。"""
        def operation(db: sqlite3.Connection) -> int:
            db.execute("INSERT OR IGNORE INTO lottery_entry(peer_id,message_id,base_fingerprint,deadline,participation_status,summary) VALUES (?,?,?,?,?,?)", (peer_id, message_id, fingerprint, deadline, status, summary))
            row = db.execute("SELECT id FROM lottery_entry WHERE peer_id=? AND message_id=?", (peer_id, message_id)).fetchone()
            assert row is not None
            entry_id = int(row["id"])
            db.execute("INSERT OR IGNORE INTO lottery_message_key(peer_id,message_id,lottery_id) VALUES (?,?,?)", (peer_id, message_id, entry_id))
            db.execute("INSERT OR IGNORE INTO lottery_content_fingerprint(lottery_id,base_fingerprint,deadline) VALUES (?,?,?)", (entry_id, fingerprint, deadline))
            return entry_id
        return self._write(operation)

    def is_duplicate(self, peer_id: int, message_id: int, fingerprint: str, deadline: str | None) -> bool:
        """优先消息幂等，再按截止缺失保守判断内容重复。"""
        with closing(self._connect()) as db:
            if db.execute("SELECT 1 FROM lottery_message_key WHERE peer_id=? AND message_id=?", (peer_id, message_id)).fetchone():
                return True
            rows = db.execute("SELECT deadline FROM lottery_content_fingerprint WHERE base_fingerprint=?", (fingerprint,)).fetchall()
        return any(deadline is None or row["deadline"] is None or row["deadline"] == deadline for row in rows)

    def prewrite_actions(self, lottery_id: int, actions: list[tuple[str, str]]) -> bool:
        """在调用 Telegram 前原子预写所有动作；重复预写返回 False。"""
        def operation(db: sqlite3.Connection) -> bool:
            for step, (action_type, signature) in enumerate(actions):
                if db.execute("INSERT OR IGNORE INTO lottery_action(lottery_id,step,action_type,source_signature,status) VALUES (?,?,?,?,?)", (lottery_id, step, action_type, signature, "PENDING")).rowcount != 1:
                    return False
            db.execute("UPDATE lottery_entry SET participation_status=? WHERE id=?", (ParticipationStatus.AUTO_PLANNED.value, lottery_id))
            return True
        return self._write(operation)

    def reserve_first_action(self, lottery_id: int, local_date: str, peer_id: int, per_group_limit: int, account_limit: int, actions: list[tuple[str, str]]) -> bool:
        """首个外部动作前一并检查冻结、占用双配额并预写动作，绝不退款。"""
        def operation(db: sqlite3.Connection) -> bool:
            if db.execute("SELECT 1 FROM lottery_group_freeze WHERE peer_id=?", (peer_id,)).fetchone(): return False
            group = db.execute("SELECT used FROM lottery_daily_quota WHERE local_date=? AND peer_id=?", (local_date, peer_id)).fetchone()
            account = db.execute("SELECT COALESCE(SUM(used),0) AS used FROM lottery_daily_quota WHERE local_date=?", (local_date,)).fetchone()["used"]
            if (group and group["used"] >= per_group_limit) or account >= account_limit: return False
            for step, (action_type, signature) in enumerate(actions):
                if db.execute("INSERT OR IGNORE INTO lottery_action(lottery_id,step,action_type,source_signature,status) VALUES (?,?,?,?,?)", (lottery_id, step, action_type, signature, "PENDING")).rowcount != 1: return False
            db.execute("INSERT INTO lottery_daily_quota(local_date,peer_id,used) VALUES (?,?,1) ON CONFLICT(local_date,peer_id) DO UPDATE SET used=used+1", (local_date, peer_id))
            db.execute("UPDATE lottery_entry SET participation_status=? WHERE id=?", (ParticipationStatus.AUTO_PLANNED.value, lottery_id))
            return True
        return self._write(operation)

    def reserve_quota(self, local_date: str, peer_id: int, per_group_limit: int, account_limit: int) -> bool:
        """兼容旧调用的原子群与账号配额占用接口。"""
        def operation(db: sqlite3.Connection) -> bool:
            group = db.execute("SELECT used FROM lottery_daily_quota WHERE local_date=? AND peer_id=?", (local_date, peer_id)).fetchone()
            account = db.execute("SELECT COALESCE(SUM(used),0) AS used FROM lottery_daily_quota WHERE local_date=?", (local_date,)).fetchone()["used"]
            if (group and group["used"] >= per_group_limit) or account >= account_limit: return False
            db.execute("INSERT INTO lottery_daily_quota(local_date,peer_id,used) VALUES (?,?,1) ON CONFLICT(local_date,peer_id) DO UPDATE SET used=used+1", (local_date, peer_id)); return True
        return self._write(operation)

    def set_action_status(self, lottery_id: int, step: int, status: str, result: str = "") -> None:
        """提交单个动作状态；未知结果冻结对应群抽奖。"""
        def operation(db: sqlite3.Connection) -> None:
            db.execute("UPDATE lottery_action SET status=?,result=? WHERE lottery_id=? AND step=?", (status, result, lottery_id, step))
            if status == "RESULT_UNKNOWN":
                row = db.execute("SELECT peer_id FROM lottery_entry WHERE id=?", (lottery_id,)).fetchone()
                if row: db.execute("INSERT INTO lottery_group_freeze(peer_id,reason,lottery_id) VALUES (?,?,?) ON CONFLICT(peer_id) DO UPDATE SET reason=excluded.reason, lottery_id=excluded.lottery_id", (row["peer_id"], status, lottery_id))
        self._write(operation)

    def set_entry_status(self, lottery_id: int, status: ParticipationStatus) -> None:
        """提交参与状态，并在 RESULT_UNKNOWN 时冻结该群抽奖。"""
        self._write(lambda db: db.execute("UPDATE lottery_entry SET participation_status=? WHERE id=?", (status.value, lottery_id)))
        if status is ParticipationStatus.RESULT_UNKNOWN:
            with closing(self._connect()) as db:
                row = db.execute("SELECT peer_id FROM lottery_entry WHERE id=?", (lottery_id,)).fetchone()
            if row: self.freeze(int(row["peer_id"]), status.value, lottery_id)

    def freeze(self, peer_id: int, reason: str, lottery_id: int | None = None) -> None:
        """冻结单群抽奖执行，不影响其他业务域。"""
        self._write(lambda db: db.execute("INSERT INTO lottery_group_freeze(peer_id,reason,lottery_id) VALUES (?,?,?) ON CONFLICT(peer_id) DO UPDATE SET reason=excluded.reason,lottery_id=excluded.lottery_id", (peer_id, reason, lottery_id)))

    def is_frozen(self, peer_id: int) -> bool:
        """返回该群是否处于抽奖冻结状态。"""
        with closing(self._connect()) as db: return db.execute("SELECT 1 FROM lottery_group_freeze WHERE peer_id=?", (peer_id,)).fetchone() is not None

    def set_pending_alert(self, lottery_id: int, summary: str) -> bool:
        """将既有抽奖设为待人工处理，并仅保存调用方提供的安全摘要。"""
        def operation(db: sqlite3.Connection) -> bool:
            return db.execute(
                "UPDATE lottery_entry SET alert_status=?, summary=? WHERE id=?",
                (AlertStatus.PENDING.value, summary, lottery_id),
            ).rowcount == 1
        return self._write(operation)

    def open_alert(self, lottery_id: int) -> LotteryEntry | None:
        """原子打开提醒，保留参与状态且仅解冻对应未知结果群。"""
        def operation(db: sqlite3.Connection) -> LotteryEntry | None:
            row = db.execute("SELECT * FROM lottery_entry WHERE id=?", (lottery_id,)).fetchone()
            if row is None: return None
            db.execute("UPDATE lottery_entry SET alert_status=? WHERE id=?", (AlertStatus.OPENED.value, lottery_id))
            if row["participation_status"] == ParticipationStatus.RESULT_UNKNOWN.value: db.execute("DELETE FROM lottery_group_freeze WHERE peer_id=?", (row["peer_id"],))
            return LotteryEntry(lottery_id, int(row["peer_id"]), int(row["message_id"]), ParticipationStatus(row["participation_status"]), AlertStatus.OPENED, row["summary"])
        return self._write(operation)

    def record_lottery_audit(self, lottery_id: int, event: str) -> None:
        """写入与抽奖记录关联的可清理审计事件。"""
        self._write(lambda db: db.execute("INSERT INTO lottery_audit_event(lottery_id,event) VALUES (?,?)", (lottery_id, event)))

    def _delete_cleanup_audit(self, db: sqlite3.Connection, ids: list[int]) -> int:
        """删除指定抽奖的关联审计，供事务故障注入测试。"""
        return db.execute(f"DELETE FROM lottery_audit_event WHERE lottery_id IN ({','.join('?' for _ in ids)})", ids).rowcount

    def cleanup_before(self, cutoff: str) -> dict[str, int]:
        """按上海自然日 cutoff 原子删除早于该日的全部抽奖关联状态。"""
        boundary = f"{cutoff}T00:00:00+08:00"
        def operation(db: sqlite3.Connection) -> dict[str, int]:
            ids = [int(row["id"]) for row in db.execute("SELECT id FROM lottery_entry WHERE deadline < ?", (boundary,))]
            if not ids: return {"lottery_action": 0, "lottery_audit_event": 0, "lottery_content_fingerprint": 0, "lottery_entry": 0, "lottery_message_key": 0}
            marks = ",".join("?" for _ in ids)
            actions = db.execute(f"DELETE FROM lottery_action WHERE lottery_id IN ({marks})", ids).rowcount
            audit = self._delete_cleanup_audit(db, ids)
            fingerprints = db.execute(f"DELETE FROM lottery_content_fingerprint WHERE lottery_id IN ({marks})", ids).rowcount
            keys = db.execute(f"DELETE FROM lottery_message_key WHERE lottery_id IN ({marks})", ids).rowcount
            entries = db.execute(f"DELETE FROM lottery_entry WHERE id IN ({marks})", ids).rowcount
            return {"lottery_action": actions, "lottery_audit_event": audit, "lottery_content_fingerprint": fingerprints, "lottery_entry": entries, "lottery_message_key": keys}
        return self._write(operation)

    def list_alerts(self, limit: int = 50, offset: int = 0) -> tuple[list[LotteryEntry], int]:
        """分页读取待处理提醒。"""
        with closing(self._connect()) as db:
            total = int(db.execute("SELECT COUNT(*) AS count FROM lottery_entry WHERE alert_status=?", (AlertStatus.PENDING.value,)).fetchone()["count"])
            rows = db.execute("SELECT * FROM lottery_entry WHERE alert_status=? ORDER BY id DESC LIMIT ? OFFSET ?", (AlertStatus.PENDING.value, limit, offset)).fetchall()
        return [LotteryEntry(int(row["id"]), int(row["peer_id"]), int(row["message_id"]), ParticipationStatus(row["participation_status"]), AlertStatus(row["alert_status"]), row["summary"]) for row in rows], total
