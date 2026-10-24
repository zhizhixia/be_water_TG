"""并发保存、旧配置错误及兼容schema升级验证。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time
import pytest

from bewater.app import create_app
from bewater.content import Messages
from bewater.engine import Engine
from bewater.lottery import Lottery
from bewater.models import DomainError, Message
from bewater.store import Store
from conftest import FakeTelegram


def test_message_compare_and_swap_is_atomic_for_two_tabs(paths, monkeypatch):
    """同一旧revision的并发编辑只能成功一次，不能静默覆盖。"""
    messages = Messages(paths)
    original = messages.load
    revision = original("parallel.txt")["revision"]
    barrier = threading.Barrier(2)

    def delayed_load(name):
        """放大读取revision后、替换前的可信竞态窗口。"""
        result = original(name)
        if result["revision"] == revision:
            time.sleep(0.04)
        return result

    monkeypatch.setattr(messages, "load", delayed_load)

    def save(text):
        """两页签同时提交同一个旧版本。"""
        barrier.wait(timeout=2)
        try:
            messages.save("parallel.txt", text, revision)
            return 200
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, text) for text in ("第一页面", "第二页面")]
        assert sorted(f.result(timeout=3) for f in futures) == [200, 409]


def test_legacy_invalid_number_has_field_error_not_health_500(paths):
    """错误值不回显，也不把旧配置导入失败变成无说明的500。"""
    paths.env_path.write_text("DAILY_LIMIT=PRIVATE_INVALID\n", encoding="utf-8")
    client = create_app(paths).test_client()
    assert client.get("/api/health").status_code == 200
    response = client.get("/api/bootstrap")
    assert response.status_code == 422 and response.json["field"] == "daily_limit"
    assert "PRIVATE_INVALID" not in response.get_data(as_text=True)


def test_schema_one_upgrade_backs_up_then_preserves_existing_record(paths, at):
    """旧候选schema1增加来源前有一致性备份，旧记录不删除、不猜来源。"""
    store = Store(paths.database)
    record = store.lottery(-1000000000123, 20, "old", None, at, "MANUAL", "", "保留")
    with store.connect(True) as db:
        db.execute("ALTER TABLE bw_lottery DROP COLUMN source")
        db.execute("UPDATE bw_meta SET value='1' WHERE key='schema'")
    upgraded = Store(paths.database)
    assert upgraded.get_lottery(record)["source"] == ""
    backup_files = list(paths.backups_dir.glob("before-v2-*.sqlite"))
    assert len(backup_files) == 1
    with sqlite3.connect(backup_files[0]) as db:
        assert db.execute("SELECT value FROM bw_meta WHERE key='schema'").fetchone()[0] == "1"
        assert "source" not in {r[1] for r in db.execute("PRAGMA table_info(bw_lottery)")}
    with upgraded.connect() as db:
        assert db.execute("SELECT value FROM bw_meta WHERE key='schema'").fetchone()[0] == "2"


@pytest.mark.asyncio
async def test_discovered_lottery_source_is_durable(setup, paths, at):
    """发现来源进入实际账本，重启后仍能区分置顶与增量。"""
    control, identity, values = setup
    gateway = FakeTelegram(identity)
    engine = Engine(control, gateway, values, clock=lambda: at)
    await engine.resolve_groups()
    message = Message(
        identity.peer_id,
        20,
        "抽奖\n奖品：耳机\n回复“+1”参与",
        at,
        7,
        pinned=True,
        source="pinned_today",
    )
    await Lottery(engine, identity).discover(message)
    row = Store(paths.database).listings("bw_lottery")["items"][0]
    assert row["source"] == "pinned_today"
