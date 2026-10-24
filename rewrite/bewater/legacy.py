"""已确认身份后的旧 SQLite 事实导入；旧表始终保留。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from telethon import utils

from bewater.models import Identity, SHANGHAI


STATUS = {
    "DETECTED": "MANUAL",
    "AUTO_PLANNED": "MANUAL",
    "EXECUTING": "UNKNOWN",
    "PARTICIPATED": "EXECUTED",
    "MANUAL_REQUIRED": "MANUAL",
    "PARTIAL": "UNKNOWN",
    "RESULT_UNKNOWN": "UNKNOWN",
    "EXPIRED": "EXPIRED",
    "DUPLICATE": "MANUAL",
}


def import_facts(db: sqlite3.Connection, identity: Identity, at: datetime) -> None:
    """先有 Telegram 实体证据，再允许旧 URL/raw ID 映射到 marked ID。"""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw = utils.resolve_id(identity.peer_id)[0]
    keys = [identity.ref, str(identity.peer_id)]
    peers = [identity.peer_id]
    if (
        "group_identity" in tables
        and db.execute(
            "SELECT 1 FROM group_identity WHERE peer_id=? AND configured_ref=?", (raw, identity.ref)
        ).fetchone()
    ):
        keys.append(str(raw))
        peers.append(raw)
    _groups(db, tables, keys, identity, at)
    _checkins(db, tables, peers, identity, at)
    _lotteries(db, tables, peers, identity, at)
    _audits(db, tables, keys, identity)
    db.execute(
        "INSERT INTO bw_audit(occurred_at,peer,event,reason) VALUES(?,?,?,?)",
        (at.isoformat(), identity.peer_id, "migration", "旧版事实已导入；旧原始表完整保留"),
    )


def _groups(db, tables, keys, identity, at) -> None:
    """保守继承计数、暂停与未确认预留，不合并未知身份。"""
    if "group_state" not in tables:
        return
    marks = ",".join("?" for _ in keys)
    rows = db.execute(f"SELECT * FROM group_state WHERE group_id IN ({marks})", keys).fetchall()
    day = at.astimezone(SHANGHAI).date().isoformat()
    count = max([int(r["sent_count"]) for r in rows if r["local_date"] == day], default=0)
    db.execute(
        "INSERT OR IGNORE INTO bw_quota VALUES(?,?,?,?)", (day, identity.peer_id, "text", count)
    )
    state = {}
    severe = next((r for r in rows if r["pause_kind"] in {"safety", "manual"}), None)
    if severe:
        state.update(pause="safety", pause_reason="从旧版保留的安全暂停，需人工检查")
    for name, new_name in (
        ("last_activity_at", "last_activity"),
        ("last_outbound_at", "last_outbound"),
    ):
        values = [r[name] for r in rows if name in r.keys() and r[name]]
        if values:
            state[new_name] = max(values)
    if state:
        db.execute(
            "UPDATE bw_group SET state=? WHERE peer=?", (json.dumps(state), identity.peer_id)
        )


def _checkins(db, tables, peers, identity, at) -> None:
    """旧账本已尝试签到不得在升级后再次自动发送。"""
    if "group_daily_task" not in tables:
        return
    marks = ",".join("?" for _ in peers)
    rows = db.execute(
        f"SELECT * FROM group_daily_task WHERE peer_id IN ({marks}) AND task_type='CHECKIN'", peers
    ).fetchall()
    for row in rows:
        entered = row["status"] not in {"PENDING", "STOPPED"}
        if not entered:
            continue
        key = f"checkin:{row['local_date']}:{identity.peer_id}"
        status = "SENT" if row["status"] == "COMPLETED" else "UNKNOWN"
        db.execute(
            "INSERT OR IGNORE INTO bw_action(action_key,peer,kind,day,status,text,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                key,
                identity.peer_id,
                "checkin",
                row["local_date"],
                status,
                "签到",
                "保留旧签到尝试，不自动重发",
                at.isoformat(),
                at.isoformat(),
            ),
        )


def _lotteries(db, tables, peers, identity, at) -> None:
    """保留所有旧消息幂等键、指纹、额度和冻结，不虚构缺失发现日期。"""
    marks = ",".join("?" for _ in peers)
    if "lottery_entry" in tables:
        rows = db.execute(
            f"SELECT * FROM lottery_entry WHERE peer_id IN ({marks})", peers
        ).fetchall()
        for row in rows:
            created = row["created_at"] if "created_at" in row.keys() else ""
            # 旧记录缺少日期不能猜成很早；保守从升级日起继续去重七日。
            created = created or at.isoformat()
            status = STATUS.get(row["participation_status"], "MANUAL")
            reason = (
                "从旧版保留；未知时间按升级日保守处理"
                if not ("created_at" in row.keys() and row["created_at"])
                else "从旧版保留"
            )
            db.execute(
                "INSERT OR IGNORE INTO bw_lottery(peer,message_id,fingerprint,deadline,created_at,status,reason,pending,summary) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identity.peer_id,
                    row["message_id"],
                    row["base_fingerprint"],
                    row["deadline"],
                    created,
                    status,
                    reason,
                    int(row["alert_status"] == "PENDING" or status == "UNKNOWN"),
                    "旧版抽奖记录",
                ),
            )
            if status == "UNKNOWN":
                db.execute(
                    "INSERT OR IGNORE INTO bw_freeze(peer,reason) VALUES(?,?)",
                    (identity.peer_id, "保留旧版未知抽奖结果，需人工检查"),
                )
    if "lottery_daily_quota" in tables:
        for row in db.execute(
            f"SELECT * FROM lottery_daily_quota WHERE peer_id IN ({marks})", peers
        ).fetchall():
            db.execute(
                "INSERT INTO bw_quota VALUES(?,?,?,?) ON CONFLICT(day,peer,kind) DO UPDATE SET used=MAX(used,excluded.used)",
                (row["local_date"], identity.peer_id, "lottery", row["used"]),
            )
    if (
        "lottery_group_freeze" in tables
        and db.execute(
            f"SELECT 1 FROM lottery_group_freeze WHERE peer_id IN ({marks})", peers
        ).fetchone()
    ):
        db.execute(
            "INSERT OR IGNORE INTO bw_freeze(peer,reason) VALUES(?,?)",
            (identity.peer_id, "保留旧版抽奖冻结，需人工检查"),
        )


def _audits(db, tables, keys, identity) -> None:
    """保留旧审计类别和安全原因，不把任意 metadata 广播到浏览器。"""
    if "audit_event" not in tables:
        return
    marks = ",".join("?" for _ in keys)
    rows = db.execute(
        f"SELECT occurred_at,event_type FROM audit_event WHERE group_id IN ({marks})", keys
    ).fetchall()
    for row in rows:
        db.execute(
            "INSERT INTO bw_audit(occurred_at,peer,event,reason) VALUES(?,?,?,?)",
            (
                row["occurred_at"],
                identity.peer_id,
                "legacy_audit",
                "旧审计类别：" + str(row["event_type"])[:80],
            ),
        )
