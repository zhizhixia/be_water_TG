"""SQLite 事实账本：统一身份、幂等外发、额度、扫描和人工待办。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from bewater.models import DomainError, Identity, SHANGHAI, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS bw_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bw_group(peer INTEGER PRIMARY KEY,ref TEXT NOT NULL,title TEXT NOT NULL,username TEXT,state TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS bw_alias(ref TEXT PRIMARY KEY,peer INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bw_quota(day TEXT,peer INTEGER,kind TEXT,used INTEGER NOT NULL,PRIMARY KEY(day,peer,kind));
CREATE TABLE IF NOT EXISTS bw_action(id INTEGER PRIMARY KEY,action_key TEXT UNIQUE NOT NULL,peer INTEGER NOT NULL,kind TEXT NOT NULL,day TEXT NOT NULL,status TEXT NOT NULL,text TEXT NOT NULL DEFAULT '',message_id INTEGER,result_id INTEGER,reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bw_batch(day TEXT PRIMARY KEY,peers TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bw_scan(peer INTEGER PRIMARY KEY,cursor INTEGER,last_attempt TEXT,last_success TEXT,error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS bw_lottery(id INTEGER PRIMARY KEY,peer INTEGER NOT NULL,message_id INTEGER NOT NULL,fingerprint TEXT NOT NULL,deadline TEXT,created_at TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',pending INTEGER NOT NULL DEFAULT 0,summary TEXT NOT NULL DEFAULT '',source TEXT NOT NULL DEFAULT '',UNIQUE(peer,message_id));
CREATE INDEX IF NOT EXISTS bw_lottery_fp ON bw_lottery(fingerprint,created_at);
CREATE TABLE IF NOT EXISTS bw_freeze(peer INTEGER PRIMARY KEY,reason TEXT NOT NULL,lottery_id INTEGER);
CREATE TABLE IF NOT EXISTS bw_audit(id INTEGER PRIMARY KEY,occurred_at TEXT NOT NULL,peer INTEGER,event TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bw_decision(peer INTEGER,trigger TEXT,PRIMARY KEY(peer,trigger));
"""


class Store:
    """事务集中在有业务含义的方法，调用方不能绕过预写发送。"""

    def __init__(self, path: Path) -> None:
        """新表独立命名，升级不删除旧版事实。"""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_before_schema()
        with self.connect() as db:
            try:
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                if "source" not in {r[1] for r in db.execute("PRAGMA table_info(bw_lottery)")}:
                    db.execute("ALTER TABLE bw_lottery ADD COLUMN source TEXT NOT NULL DEFAULT ''")
                db.execute(
                    "INSERT INTO bw_meta VALUES('schema','2') ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _backup_before_schema(self) -> None:
        """变更旧库前先做 SQLite 一致性备份；未知新版本零 schema 改动。"""
        if not self.path.exists() or not self.path.stat().st_size:
            return
        from contextlib import closing
        import tempfile

        with closing(
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as source:
            tables = {
                row[0]
                for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "bw_meta" in tables:
                row = source.execute("SELECT value FROM bw_meta WHERE key='schema'").fetchone()
                if row is None or row[0] not in {"1", "2"}:
                    raise DomainError("数据库版本不受支持，禁止继续运行", status=409)
                if row[0] == "2":
                    return
            if not tables:
                return
            directory = self.path.parent.parent / "backups"
            directory.mkdir(parents=True, exist_ok=True)
            handle, filename = tempfile.mkstemp(
                prefix="before-v2-", suffix=".sqlite", dir=directory
            )
            import os

            os.close(handle)
            try:
                with closing(sqlite3.connect(filename)) as target:
                    source.backup(target)
            except BaseException:
                Path(filename).unlink(missing_ok=True)
                raise

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """显式提交或回滚整个事务，关闭每个连接。"""
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def audit(self, peer: int | None, event: str, reason: str = "") -> None:
        """只记录安全固定原因，不接收凭据或原始 callback。"""
        with self.connect(True) as db:
            db.execute(
                "INSERT INTO bw_audit(occurred_at,peer,event,reason) VALUES(?,?,?,?)",
                (now().isoformat(), peer, event, reason[:500]),
            )

    def identity(self, identity: Identity, at: datetime) -> None:
        """保存确认后的群身份并保守导入旧 URL/peer 状态。"""
        with self.connect(True) as db:
            previous = db.execute(
                "SELECT state FROM bw_group WHERE peer=?", (identity.peer_id,)
            ).fetchone()
            db.execute(
                "INSERT INTO bw_group(peer,ref,title,username) VALUES(?,?,?,?) ON CONFLICT(peer) DO UPDATE SET ref=excluded.ref,title=excluded.title,username=excluded.username",
                (identity.peer_id, identity.ref, identity.title, identity.username),
            )
            db.execute(
                "INSERT OR REPLACE INTO bw_alias VALUES(?,?)", (identity.ref, identity.peer_id)
            )
            if previous is None:
                self._import_legacy(db, identity, at)

    def _import_legacy(self, db: sqlite3.Connection, identity: Identity, at: datetime) -> None:
        """确认实体后一次性导入旧事实，源表不删除不改写。"""
        from bewater.legacy import import_facts

        import_facts(db, identity, at)

    def state(self, peer: int) -> dict[str, object]:
        """读取一个群的持久化观察状态。"""
        with self.connect() as db:
            row = db.execute("SELECT state FROM bw_group WHERE peer=?", (peer,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_state(self, peer: int, **changes: object) -> None:
        """事务内合并状态，不丢失其他线程刚写入的字段。"""
        with self.connect(True) as db:
            row = db.execute("SELECT state FROM bw_group WHERE peer=?", (peer,)).fetchone()
            if row is None:
                raise DomainError("群身份尚未建立", status=409)
            value = {**json.loads(row[0]), **changes}
            db.execute(
                "UPDATE bw_group SET state=? WHERE peer=?",
                (json.dumps(value, ensure_ascii=False), peer),
            )

    def decide_once(self, peer: int, trigger: str) -> bool:
        """同一触发只决策一次，轮询不能提高命中概率。"""
        with self.connect(True) as db:
            return (
                db.execute(
                    "INSERT OR IGNORE INTO bw_decision VALUES(?,?)", (peer, trigger)
                ).rowcount
                == 1
            )

    def quota(self, day: str, peer: int, kind: str = "text") -> int:
        """按上海自然日读取持久化已使用额度。"""
        with self.connect() as db:
            row = db.execute(
                "SELECT used FROM bw_quota WHERE day=? AND peer=? AND kind=?", (day, peer, kind)
            ).fetchone()
        return int(row[0]) if row else 0

    def reserve(
        self,
        key: str,
        peer: int,
        kind: str,
        text: str,
        message_id: int | None,
        at: datetime,
        group_limit: int,
        account_limit: int = 0,
    ) -> int | None:
        """幂等检查、群/账号额度和动作预写在同一事务完成。"""
        day, quota_kind = (
            at.astimezone(SHANGHAI).date().isoformat(),
            "lottery" if kind == "lottery" else "text",
        )
        with self.connect(True) as db:
            if db.execute("SELECT 1 FROM bw_action WHERE action_key=?", (key,)).fetchone():
                return None
            if (
                kind == "lottery"
                and db.execute("SELECT 1 FROM bw_freeze WHERE peer=?", (peer,)).fetchone()
            ):
                return None
            row = db.execute(
                "SELECT used FROM bw_quota WHERE day=? AND peer=? AND kind=?",
                (day, peer, quota_kind),
            ).fetchone()
            total = db.execute(
                "SELECT COALESCE(SUM(used),0) FROM bw_quota WHERE day=? AND kind=?",
                (day, quota_kind),
            ).fetchone()[0]
            if (row and row[0] >= group_limit) or (account_limit and total >= account_limit):
                return None
            db.execute(
                "INSERT INTO bw_quota VALUES(?,?,?,1) ON CONFLICT(day,peer,kind) DO UPDATE SET used=used+1",
                (day, peer, quota_kind),
            )
            row = db.execute(
                "INSERT INTO bw_action(action_key,peer,kind,day,status,text,message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (key, peer, kind, day, "READY", text, message_id, at.isoformat(), at.isoformat()),
            )
            return int(row.lastrowid)

    def action_status(
        self, action_id: int, status: str, reason: str = "", result_id: int | None = None
    ) -> None:
        """落盘网络调用阶段与结果；未知结果不会自动退款。"""
        with self.connect(True) as db:
            db.execute(
                "UPDATE bw_action SET status=?,reason=?,result_id=?,updated_at=? WHERE id=?",
                (status, reason, result_id, now().isoformat(), action_id),
            )

    def cancel_ready(self, action_id: int) -> None:
        """仅尚未进入网络调用的预留可撤销。"""
        with self.connect(True) as db:
            row = db.execute(
                "SELECT * FROM bw_action WHERE id=? AND status='READY'", (action_id,)
            ).fetchone()
            if not row:
                return
            kind = "lottery" if row["kind"] == "lottery" else "text"
            db.execute(
                "UPDATE bw_quota SET used=MAX(0,used-1) WHERE day=? AND peer=? AND kind=?",
                (row["day"], row["peer"], kind),
            )
            db.execute("DELETE FROM bw_action WHERE id=?", (action_id,))

    def recover(self) -> None:
        """重启后已进入调用的记录保持未知，未发出预留可安全释放。"""
        with self.connect() as db:
            ready = [r[0] for r in db.execute("SELECT id FROM bw_action WHERE status='READY'")]
        for action_id in ready:
            self.cancel_ready(action_id)
        with self.connect(True) as db:
            # 仅冻结本次中断的调用，不重新处理已经人工确认的历史 UNKNOWN。
            db.execute(
                "INSERT OR IGNORE INTO bw_freeze(peer,reason) SELECT DISTINCT peer,'恢复时发现中断抽奖调用' FROM bw_action WHERE status='CALLING' AND kind='lottery'"
            )
            db.execute(
                "INSERT OR IGNORE INTO bw_freeze(peer,reason,lottery_id) SELECT peer,'恢复时发现中断抽奖调用',id FROM bw_lottery WHERE status='EXECUTING'"
            )
            db.execute(
                "UPDATE bw_lottery SET status='UNKNOWN',pending=1,reason='调用中断，需人工检查' WHERE status='EXECUTING' OR EXISTS(SELECT 1 FROM bw_action a WHERE a.peer=bw_lottery.peer AND a.message_id=bw_lottery.message_id AND a.kind='lottery' AND a.status='CALLING')"
            )
            db.execute(
                "UPDATE bw_action SET status='UNKNOWN',reason='进程中断，禁止自动重试' WHERE status='CALLING'"
            )

    def batch(self, day: str, peers: list[int]) -> list[int]:
        """冻结当天群快照，并与当前授权范围求交集。"""
        with self.connect(True) as db:
            db.execute("INSERT OR IGNORE INTO bw_batch VALUES(?,?)", (day, json.dumps(peers)))
            saved = json.loads(
                db.execute("SELECT peers FROM bw_batch WHERE day=?", (day,)).fetchone()[0]
            )
        return [p for p in saved if p in peers]

    def recent_texts(self, peer: int) -> list[str]:
        """发送成功历史跨重启用于去重。"""
        with self.connect() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT text FROM bw_action WHERE peer=? AND status='SENT' AND kind='chat' ORDER BY id DESC LIMIT 20",
                    (peer,),
                )
            ]

    def scan(self, peer: int) -> dict[str, object]:
        """读取扫描游标与健康信息。"""
        with self.connect() as db:
            row = db.execute("SELECT * FROM bw_scan WHERE peer=?", (peer,)).fetchone()
        return dict(row) if row else {"peer": peer, "cursor": None}

    def checkpoint(self, peer: int, cursor: int, at: datetime, error: str = "") -> None:
        """完整处理一页后才单调推进检查点。"""
        with self.connect(True) as db:
            db.execute(
                "INSERT INTO bw_scan VALUES(?,?,?,?,?) ON CONFLICT(peer) DO UPDATE SET cursor=MAX(COALESCE(cursor,0),excluded.cursor),last_attempt=excluded.last_attempt,last_success=CASE WHEN excluded.error='' THEN excluded.last_success ELSE last_success END,error=excluded.error",
                (peer, cursor, at.isoformat(), at.isoformat(), error),
            )

    def scan_failure(self, peer: int, at: datetime) -> None:
        """失败也展示检查时间和固定错误类别，但绝不推进游标。"""
        with self.connect(True) as db:
            db.execute(
                "INSERT INTO bw_scan(peer,last_attempt,error) VALUES(?,?,?) ON CONFLICT(peer) DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error",
                (peer, at.isoformat(), "扫描暂时失败，未跳过未处理消息"),
            )

    def find_lottery(
        self, peer: int, message_id: int, fingerprint: str, deadline: str | None, at: datetime
    ) -> int | None:
        """同消息永久去重，内容指纹仅跨群保守去重七日。"""
        with self.connect() as db:
            exact = db.execute(
                "SELECT id FROM bw_lottery WHERE peer=? AND message_id=?", (peer, message_id)
            ).fetchone()
            if exact:
                return int(exact[0])
            rows = db.execute(
                "SELECT id,deadline FROM bw_lottery WHERE fingerprint=? AND created_at>?",
                (fingerprint, (at - timedelta(days=7)).isoformat()),
            ).fetchall()
        return next(
            (
                int(r["id"])
                for r in rows
                if deadline is None or r["deadline"] is None or deadline == r["deadline"]
            ),
            None,
        )

    def lottery(
        self,
        peer: int,
        message_id: int,
        fingerprint: str,
        deadline: str | None,
        at: datetime,
        status: str,
        reason: str,
        summary: str,
        source: str = "",
    ) -> int:
        """持久化发现时间与独立的参与/提醒状态。"""
        with self.connect(True) as db:
            row = db.execute(
                "INSERT INTO bw_lottery(peer,message_id,fingerprint,deadline,created_at,status,reason,pending,summary,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    peer,
                    message_id,
                    fingerprint,
                    deadline,
                    at.isoformat(),
                    status,
                    reason,
                    int(status in {"MANUAL", "UNKNOWN"}),
                    summary[:120],
                    source,
                ),
            )
            return int(row.lastrowid)

    def lottery_result(self, lottery_id: int, status: str, reason: str = "") -> None:
        """记录抽奖终态；未知结果在同事务内冻结本群。"""
        with self.connect(True) as db:
            db.execute(
                "UPDATE bw_lottery SET status=?,reason=?,pending=? WHERE id=?",
                (status, reason, int(status in {"MANUAL", "UNKNOWN"}), lottery_id),
            )
            if status == "UNKNOWN":
                row = db.execute("SELECT peer FROM bw_lottery WHERE id=?", (lottery_id,)).fetchone()
                db.execute(
                    "INSERT OR REPLACE INTO bw_freeze VALUES(?,?,?)", (row[0], reason, lottery_id)
                )

    def frozen(self, peer: int) -> bool:
        """抽奖冻结不扩展到其他业务。"""
        with self.connect() as db:
            return (
                db.execute("SELECT 1 FROM bw_freeze WHERE peer=?", (peer,)).fetchone() is not None
            )

    def unfreeze(self, peer: int) -> None:
        """明确人工恢复，不改变任何旧动作结果。"""
        with self.connect(True) as db:
            db.execute("DELETE FROM bw_freeze WHERE peer=?", (peer,))
        self.audit(peer, "lottery_resumed", "人工确认后恢复，未知动作不重试")

    def listings(
        self, table: str, offset: int = 0, limit: int = 50, pending: bool = False
    ) -> dict[str, object]:
        """仅查询固定白名单表，提供稳定分页与总数。"""
        if table not in {"bw_lottery", "bw_audit", "bw_action"}:
            raise DomainError("查询类别无效")
        where = " WHERE pending=1" if pending and table == "bw_lottery" else ""
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM {table}{where}").fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            item.pop("text", None)
            item.pop("fingerprint", None)
            item.pop("action_key", None)
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def get_lottery(self, lottery_id: int) -> dict[str, object]:
        """读取指定提醒供校验消息链接使用。"""
        with self.connect() as db:
            row = db.execute("SELECT * FROM bw_lottery WHERE id=?", (lottery_id,)).fetchone()
        if not row:
            raise DomainError("没有找到该抽奖", status=404)
        return dict(row)

    def mark_viewed(self, lottery_id: int) -> None:
        """人工确认已查看后关闭提醒，不解冻、不改账本或额度。"""
        with self.connect(True) as db:
            db.execute("UPDATE bw_lottery SET pending=0 WHERE id=?", (lottery_id,))

    def cleanup(self, before: str, *, preview: bool = False) -> dict[str, int]:
        """按发现日期清理历史，删除过程全部成功才提交。"""
        boundary = datetime.fromisoformat(before).replace(tzinfo=SHANGHAI).isoformat()
        with self.connect(not preview) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM bw_lottery WHERE created_at<?", (boundary,)
            ).fetchone()[0]
            if not preview:
                db.execute(
                    "DELETE FROM bw_action WHERE kind='lottery' AND created_at<?", (boundary,)
                )
                db.execute("DELETE FROM bw_lottery WHERE created_at<?", (boundary,))
            return {"lotteries": count}
