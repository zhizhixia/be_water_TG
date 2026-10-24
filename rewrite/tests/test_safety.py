"""安全边界与升级失败路径的补充反例。"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import types
from telethon.errors import FloodWaitError

from bewater.models import DomainError
from bewater.policy import fixed_text_safe
from bewater.store import Store
from bewater.telegram import Telegram


@pytest.mark.parametrize(
    "text",
    [
        "电话13800138000",
        "138 0013 8000",
        "１２３４５６７８９",
        "发给test@example.com",
        "访问https://example.com",
    ],
)
def test_fixed_text_rejects_disguised_contact_and_links(text):
    """中文包围和空格分隔不能绕过手机号拒绝规则。"""
    assert not fixed_text_safe(text)


def test_future_schema_does_not_modify_database_tables(paths):
    """未知更高版本必须在创建任何新版表之前拒绝。"""
    db = sqlite3.connect(paths.database)
    db.execute("CREATE TABLE bw_meta(key TEXT PRIMARY KEY,value TEXT)")
    db.execute("INSERT INTO bw_meta VALUES('schema','999')")
    db.commit()
    db.close()
    with pytest.raises(DomainError):
        Store(paths.database)
    db = sqlite3.connect(paths.database)
    try:
        assert [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")] == [
            "bw_meta"
        ]
    finally:
        db.close()


def test_old_database_gets_restorable_backup_before_new_schema(paths):
    """备份里有原始事实、没有新版表，不只验证备份文件存在。"""
    db = sqlite3.connect(paths.database)
    db.execute("CREATE TABLE sample(value TEXT)")
    db.execute("INSERT INTO sample VALUES('旧事实')")
    db.commit()
    db.close()
    Store(paths.database)
    backups = list(paths.backups_dir.glob("before-v2-*.sqlite"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    try:
        assert backup.execute("SELECT value FROM sample").fetchone()[0] == "旧事实"
        assert not backup.execute("SELECT 1 FROM sqlite_master WHERE name='bw_action'").fetchone()
    finally:
        backup.close()


def test_crash_between_call_marker_and_lottery_state_is_unknown(setup, at):
    """调用已预写但抽奖状态尚未更新就崩溃，也必须冻结而非恢复旧计划。"""
    control, identity, _ = setup
    record = control.store.lottery(identity.peer_id, 3, "crash", None, at, "PLANNED", "", "测试")
    action = control.store.reserve("lottery-crash", identity.peer_id, "lottery", "", 3, at, 10, 30)
    control.store.action_status(action, "CALLING")
    control.store.recover()
    assert control.store.get_lottery(record)["status"] == "UNKNOWN"
    assert control.store.frozen(identity.peer_id)
    assert control.store.quota(at.date().isoformat(), identity.peer_id, "lottery") == 1


@pytest.mark.asyncio
async def test_permission_failure_manual_but_floodwait_propagates(at):
    """普通查询失败是未知信任，限流必须交给统一冷却而非吞掉。"""
    gateway = Telegram.__new__(Telegram)
    gateway.peers = {-1000000000123: object()}
    gateway.client = SimpleNamespace(get_entity=AsyncMock(side_effect=OSError("匿名网络错误")))
    assert await gateway.trust(-1000000000123, 9) == "unknown"
    gateway.client.get_entity.side_effect = FloodWaitError(None, 20)
    with pytest.raises(FloodWaitError):
        await gateway.trust(-1000000000123, 9)


@pytest.mark.asyncio
async def test_banned_bot_is_not_trusted(at):
    """bot 身份不能覆盖已经被禁或离开的群成员事实。"""
    entity = types.Channel(
        123, "匿名群", types.ChatPhotoEmpty(), at, megagroup=True, access_hash=456
    )
    gateway = Telegram.__new__(Telegram)
    gateway.peers = {-1000000000123: object()}
    gateway.client = SimpleNamespace(
        get_entity=AsyncMock(return_value=entity),
        get_input_entity=AsyncMock(return_value=types.InputPeerChannel(123, 456)),
        get_permissions=AsyncMock(
            return_value=SimpleNamespace(
                has_left=False, is_banned=True, is_creator=False, is_admin=False
            )
        ),
    )
    assert await gateway.trust(-1000000000123, 9) == "unknown"
