"""真实 SDK 类型、认证挑战、旧库迁移与任务控制的契约检查。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import types
from telethon.errors import SessionPasswordNeededError
from telethon.tl.custom.message import Message as TLMessage

from bewater.app import create_app
from bewater.models import Identity
from bewater.runtime import FileLock
from bewater.store import Store
from bewater.telegram import Telegram


@pytest.mark.asyncio
async def test_real_tl_buttons_and_typed_channel_entity(at):
    """真实 Telethon Message、Channel 和 InputPeerChannel 进入同一生产适配器。"""
    entity = types.Channel(
        123, "匿名群", types.ChatPhotoEmpty(), at, megagroup=True, access_hash=456
    )
    input_peer = types.InputPeerChannel(123, 456)
    client = SimpleNamespace(
        get_entity=AsyncMock(return_value=entity),
        get_input_entity=AsyncMock(return_value=input_peer),
    )
    gateway = Telegram.__new__(Telegram)
    gateway.client = client
    gateway.peers = {}
    gateway.me = 9
    identity = await gateway.resolve("https://t.me/test_group")
    assert identity.peer_id == -1000000000123
    assert gateway._peer(identity.peer_id) is input_peer
    raw = TLMessage(id=5, peer_id=types.PeerChannel(123), date=at, message="抽奖", out=False)
    raw._buttons = [[types.KeyboardButtonCallback("参与", b"go")]]
    raw._buttons_count = 1
    snapshot = gateway.snapshot(identity.peer_id, raw)
    assert snapshot.buttons[0][0].kind == "callback"
    assert snapshot.buttons[0][0].data == b"go"


@pytest.mark.asyncio
async def test_sdk_rejects_broadcast_and_user_entities(at):
    """白名单 URL 解析结果也必须再次检查实体类型。"""
    for entity in (
        types.User(9),
        types.Channel(123, "广播", types.ChatPhotoEmpty(), at, broadcast=True),
    ):
        gateway = Telegram.__new__(Telegram)
        gateway.peers = {}
        gateway.client = SimpleNamespace(get_entity=AsyncMock(return_value=entity))
        with pytest.raises(ValueError):
            await gateway.resolve("https://t.me/test_group")


@pytest.mark.asyncio
async def test_sdk_auth_switches_to_two_step_password_without_persisting():
    """SDK 要求两步验证时提出新挑战，不把密码当验证码重试。"""
    gateway = Telegram.__new__(Telegram)
    gateway.values = {"phone": "+10000000000"}
    gateway.client = SimpleNamespace(
        sign_in=AsyncMock(side_effect=[SessionPasswordNeededError(None), None])
    )
    seen = []

    async def challenge(kind):
        """仅生成公开的匿名测试值。"""
        seen.append(kind)
        return "test-only-not-real"

    await gateway._authenticate(challenge)
    assert seen == ["code", "password"]
    assert gateway.client.sign_in.call_args_list[1].kwargs == {"password": "test-only-not-real"}


def test_os_lock_releases_on_handle_close(tmp_path):
    """互斥依赖 OS 而不是猜测锁文件年龄，关闭即可再次获取。"""
    first, second = FileLock(tmp_path / "same.lock"), FileLock(tmp_path / "same.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


@pytest.mark.asyncio
async def test_auth_challenge_is_generation_bound_and_cancellable(setup):
    """旧挑战、重复输入和停止不能把后台重新唤醒为运行态。"""
    control, _, _ = setup
    control.loop = asyncio.get_running_loop()
    task = asyncio.create_task(control.ask("code"))
    await asyncio.sleep(0)
    challenge_id = control.challenge["id"]
    with pytest.raises(ValueError):
        control.submit("wrong-id", "test")
    control.submit(challenge_id, "public-test-code")
    assert await task == "public-test-code"
    with pytest.raises(ValueError):
        control.submit(challenge_id, "test")
    task = asyncio.create_task(control.ask("password"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert control.challenge is None


def test_schema_upgrade_keeps_old_quota_checkin_history_and_freeze(paths, at):
    """在合成旧库上演练，不对真实数据库产生写操作。"""
    db = sqlite3.connect(paths.database)
    db.executescript("""
        CREATE TABLE group_state(group_id TEXT,local_date TEXT,sent_count INTEGER,pause_kind TEXT,last_activity_at TEXT,last_outbound_at TEXT);
        CREATE TABLE group_daily_task(peer_id INTEGER,local_date TEXT,task_type TEXT,status TEXT);
        CREATE TABLE lottery_entry(id INTEGER,peer_id INTEGER,message_id INTEGER,base_fingerprint TEXT,deadline TEXT,participation_status TEXT,alert_status TEXT,created_at TEXT);
        CREATE TABLE lottery_daily_quota(local_date TEXT,peer_id INTEGER,used INTEGER);
        CREATE TABLE lottery_group_freeze(peer_id INTEGER,reason TEXT,lottery_id INTEGER);
        CREATE TABLE audit_event(occurred_at TEXT,group_id TEXT,event_type TEXT);
    """)
    peer = -1000000000123
    day = at.date().isoformat()
    db.execute(
        "INSERT INTO group_state VALUES(?,?,?,?,?,?)",
        ("https://t.me/allowed_group", day, 17, "safety", at.isoformat(), at.isoformat()),
    )
    db.execute("INSERT INTO group_daily_task VALUES(?,?,?,?)", (peer, day, "CHECKIN", "FAILED"))
    db.execute(
        "INSERT INTO lottery_entry VALUES(?,?,?,?,?,?,?,?)",
        (1, peer, 50, "preserved", None, "RESULT_UNKNOWN", "PENDING", ""),
    )
    db.execute("INSERT INTO lottery_daily_quota VALUES(?,?,?)", (day, peer, 4))
    db.execute("INSERT INTO lottery_group_freeze VALUES(?,?,?)", (peer, "unknown", 1))
    db.commit()
    db.close()
    store = Store(paths.database)
    identity = Identity("https://t.me/allowed_group", peer, "匿名群")
    store.identity(identity, at)
    store.identity(identity, at)
    assert store.quota(day, peer) == 17
    assert store.quota(day, peer, "lottery") == 4
    assert store.state(peer)["pause"] == "safety"
    assert store.frozen(peer)
    assert store.listings("bw_lottery")["total"] == 1
    assert store.reserve(f"checkin:{day}:{peer}", peer, "checkin", "签到", None, at, 30) is None
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM lottery_entry").fetchone()[0] == 1
        assert connection.execute("SELECT sent_count FROM group_state").fetchone()[0] == 17


def test_cleanup_by_discovery_day_not_deadline_and_state_guard(setup, at, paths):
    """无截止记录能按发现日清理；运行中与未确认操作被拒绝。"""
    control, identity, _ = setup
    control.store.lottery(
        identity.peer_id, 10, "old", None, at - timedelta(days=3), "MANUAL", "", "旧记录"
    )
    control.store.lottery(identity.peer_id, 11, "new", None, at, "MANUAL", "", "新记录")
    app = create_app(paths)
    client = app.test_client()
    csrf = client.get("/api/bootstrap").get_json()["csrf"]
    headers = {"X-CSRF-Token": csrf}
    body = {"before": at.date().isoformat(), "preview": True}
    assert client.post("/api/cleanup", json=body, headers=headers).get_json()["lotteries"] == 1
    assert (
        client.post("/api/cleanup", json={"before": body["before"]}, headers=headers).status_code
        == 422
    )
    app.extensions["controller"].thread = object()
    assert client.post("/api/cleanup", json=body, headers=headers).status_code == 409
    app.extensions["controller"].thread = None
    result = client.post(
        "/api/cleanup", json={"before": body["before"], "confirm": "确认清理"}, headers=headers
    )
    assert result.status_code == 200
    assert control.store.listings("bw_lottery")["total"] == 1


def test_private_channel_link_and_open_do_not_unfreeze(setup, at, paths):
    """正确构造私有群消息链接；打开提醒不是安全解冻。"""
    control, identity, _ = setup
    with control.store.connect(True) as db:
        db.execute("UPDATE bw_group SET username=NULL")
    record = control.store.lottery(identity.peer_id, 7, "link", None, at, "UNKNOWN", "未知", "测试")
    control.store.lottery_result(record, "UNKNOWN", "未知")
    client = create_app(paths).test_client()
    csrf = client.get("/api/bootstrap").get_json()["csrf"]
    result = client.post(f"/api/lotteries/{record}/open", json={}, headers={"X-CSRF-Token": csrf})
    assert result.get_json()["url"] == "https://t.me/c/123/7"
    assert result.get_json()["frozen"] is True
    assert control.store.get_lottery(record)["pending"] == 1


def test_mark_viewed_requires_explicit_confirm_and_keeps_unknown_state(setup, at, paths):
    """打开链接不消提醒；只有布尔确认的独立接口关闭提醒，不解冻也不改账本。"""
    control, identity, _ = setup
    peer = identity.peer_id
    record = control.store.lottery(peer, 12, "viewed", None, at, "UNKNOWN", "未知结果", "测试")
    control.store.lottery_result(record, "UNKNOWN", "未知结果")
    app = create_app(paths)
    client = app.test_client()
    csrf = client.get("/api/bootstrap").get_json()["csrf"]
    headers = {"X-CSRF-Token": csrf}
    opened = client.post(f"/api/lotteries/{record}/open", json={}, headers=headers)
    assert opened.status_code == 200
    assert control.store.get_lottery(record)["pending"] == 1
    assert control.store.frozen(peer)
    url = f"/api/lotteries/{record}/viewed"
    assert client.post(url, json={"confirm": True}).status_code == 403
    cross_site = client.post(
        url,
        json={"confirm": True},
        headers={**headers, "Origin": "https://example.test"},
    )
    assert cross_site.status_code == 403
    for value in (1, "true", "确认已查看", None, False, [True]):
        response = client.post(url, json={"confirm": value}, headers=headers)
        assert response.status_code == 422
    assert control.store.get_lottery(record)["pending"] == 1
    missing = client.post("/api/lotteries/999999/viewed", json={"confirm": True}, headers=headers)
    assert missing.status_code == 404
    first = client.post(url, json={"confirm": True}, headers=headers)
    assert first.status_code == 200 and first.get_json() == {"ok": True}
    saved = control.store.get_lottery(record)
    assert saved["pending"] == 0 and saved["status"] == "UNKNOWN"
    assert control.store.frozen(peer)
    with control.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bw_action").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM bw_quota").fetchone()[0] == 0
        assert db.execute("SELECT lottery_id FROM bw_freeze WHERE peer=?", (peer,)).fetchone()[0] == record
    again = client.post(url, json={"confirm": True}, headers=headers)
    assert again.status_code == 200
    assert control.store.get_lottery(record)["pending"] == 0


def test_sse_reconnect_sends_complete_snapshot_first(paths):
    """旧进程序号失效时先恢复真实完整快照而非假在线状态。"""
    client = create_app(paths).test_client()
    response = client.get(
        "/api/events", headers={"Last-Event-ID": "old-generation:999999"}, buffered=False
    )
    chunk = next(iter(response.response))
    assert b"event: snapshot" in chunk and b'"state": "stopped"' in chunk
    response.close()
