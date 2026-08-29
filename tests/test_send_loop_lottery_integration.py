"""主循环与真实抽奖组件的安全接线回归。"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from src.config import Settings
from src.group_identity import GroupIdentity
from src.lottery_models import MessageSnapshot
from src.state_store import StateStore
from ui.send_loop import _send_with_retry, send_loop


def test_startup_locks_migrates_and_uses_only_peer_ids_for_checkin_and_state(
    monkeypatch, tmp_path
) -> None:
    """主循环必须在锁后迁移，并让签到和状态仓储只收到稳定 peer_id。"""
    events: list[object] = []

    class Lock:
        def __init__(self, *_args: object) -> None:
            events.append("lock_created")

        def acquire(self) -> bool:
            events.append("lock")
            return True

        def release(self) -> None:
            events.append("release")

    class RecordingStore(StateStore):
        def migrate_group_identities(self, identities: list[object]) -> None:
            events.append(("migrate", [identity.peer_id for identity in identities]))
            super().migrate_group_identities(identities)

        def reconcile_pending_reservations(self, groups: list[str], daily_limit: int):
            events.append(("reconcile", groups))
            return super().reconcile_pending_reservations(groups, daily_limit)

    monkeypatch.setattr("ui.send_loop.InstanceLock", Lock)
    settings = Settings(
        api_id=1, api_hash="x" * 32, phone="+1", target_groups=["https://t.me/allowed"],
        state_db_path=str(tmp_path / "state.db"), lottery_enabled=False, daily_checkin_enabled=True,
    )
    manager = MagicMock(stop_event=threading.Event())
    manager.runtime_counts_snapshot.return_value = (0, {})
    sender = MagicMock()
    sender.resolve_group_identity = AsyncMock(return_value=GroupIdentity(101, "https://t.me/allowed", "允许群"))
    async def checkin(peer_id: int, text: str) -> int:
        manager.stop_event.set()
        return 1

    sender.send_group_text = AsyncMock(side_effect=checkin)
    store = RecordingStore(settings.state_db_path)

    asyncio.run(send_loop(sender, settings, manager, MagicMock(), state_store=store))

    assert events[:3] == ["lock_created", "lock", ("migrate", [101])]
    assert events[-1] == "release"
    assert sender.send_group_text.await_args.args == (101, "签到")
    assert ("reconcile", ["101"]) in events
    assert store.get_group_state("101").sent_count == 1
    assert store.list_group_states(["https://t.me/allowed"])[0].sent_count == 0


def _lottery_settings(tmp_path, interval_minutes: int, pinned_enabled: bool = True) -> Settings:
    """构造只扫描稳定 peer_id 的抽奖主循环配置。"""
    settings = Settings(
        api_id=1,
        api_hash="x" * 32,
        phone="+1",
        target_groups=["https://t.me/allowed"],
        state_db_path=str(tmp_path / "state.db"),
        lottery_enabled=True,
        daily_checkin_enabled=False,
    )
    settings.lottery_scan_interval_minutes = interval_minutes
    settings.daily_pinned_lottery_scan_enabled = pinned_enabled
    return settings


def _lottery_manager() -> MagicMock:
    """构造运行态管理器，避免测试触发真实网络或普通参与发送。"""
    manager = MagicMock()
    manager.stop_event = threading.Event()
    manager.state = "running"
    manager.runtime_counts_snapshot.return_value = (0, {})
    return manager


def _lottery_sender() -> MagicMock:
    """构造仅记录扫描调用的发送器 fake。"""
    sender = MagicMock()
    sender.resolve_group_identity = AsyncMock(
        return_value=GroupIdentity(101, "https://t.me/allowed", "允许群")
    )
    sender.get_current_pinned_messages = AsyncMock(return_value=([], True))
    sender.get_latest_message_id = AsyncMock(return_value=10)
    sender.get_messages_after = AsyncMock(return_value=[])
    return sender


def _install_lottery_store(monkeypatch, initial_cursor: int | None = None) -> None:
    """安装只实现扫描所需接口的内存仓储。"""
    class LotteryStoreFake:
        cursor = initial_cursor

        def __init__(self, _path: str) -> None:
            pass

        def daily_batch_groups(self, _today: str) -> list[int]:
            return [101]

        def create_daily_batch(self, _today: str, _peers: list[int]) -> None:
            pass

        def is_frozen(self, _peer: int) -> bool:
            return False

        def scan_state(self, _peer: int) -> dict[str, int | None]:
            return {"cursor_message_id": type(self).cursor}

        def commit_scan_batch(self, _peer: int, latest: int, _messages: list[object]) -> None:
            type(self).cursor = latest

    monkeypatch.setattr("ui.send_loop.LotteryStore", LotteryStoreFake)


def test_lottery_scans_each_peer_only_after_its_configured_full_interval(
    monkeypatch, tmp_path
) -> None:
    """同群初扫后，1 和 2 分钟配置分别在对应完整门槛才允许重扫。"""
    async def run(interval_minutes: int) -> int:
        now = 0.0
        sender = _lottery_sender()
        manager = _lottery_manager()
        settings = _lottery_settings(tmp_path / str(interval_minutes), interval_minutes)
        advances = iter([interval_minutes * 60 - 1, 1, 0])
        wait_calls = 0
        _install_lottery_store(monkeypatch)

        async def wait(_seconds: float, _stop_event: object, _event_bus: object = None) -> bool:
            nonlocal now, wait_calls
            now += next(advances)
            wait_calls += 1
            if wait_calls == 3:
                manager.stop_event.set()
            return not manager.stop_event.is_set()

        monkeypatch.setattr("ui.send_loop._interruptible_wait", wait)
        await send_loop(
            sender,
            settings,
            manager,
            MagicMock(),
            lottery_scan_clock=lambda: now,
        )
        return sender.get_current_pinned_messages.await_count

    assert asyncio.run(run(1)) == 2
    assert asyncio.run(run(2)) == 2


def test_lottery_pinned_filter_uses_shanghai_date(monkeypatch, tmp_path) -> None:
    """UTC 尚属前一天时，置顶筛选仍以已跨日的上海日期为准。"""
    sender = _lottery_sender()
    sender.get_current_pinned_messages.return_value = ([
        MessageSnapshot(101, 7, "抽奖", occurred_at="2026-08-29T00:01:00+08:00", pinned=True),
    ], True)
    manager = _lottery_manager()
    settings = _lottery_settings(tmp_path, 60)
    detected: list[int] = []
    _install_lottery_store(monkeypatch)

    async def detect(message: MessageSnapshot, _ai: object) -> SimpleNamespace:
        detected.append(message.message_id)
        return SimpleNamespace(is_lottery=False)

    async def wait(_seconds: float, _stop_event: object, _event_bus: object = None) -> bool:
        manager.stop_event.set()
        return False

    monkeypatch.setattr("ui.send_loop.detect_with_ai", detect)
    monkeypatch.setattr("ui.send_loop._interruptible_wait", wait)
    asyncio.run(send_loop(
        sender,
        settings,
        manager,
        MagicMock(),
        lottery_scan_clock=lambda: 0.0,
        shanghai_now=lambda: datetime(2026, 8, 28, 16, 1, tzinfo=ZoneInfo("UTC")),
    ))

    assert detected == [7]


def test_lottery_pinned_switch_skips_pinned_read_but_keeps_incremental_scan(
    monkeypatch, tmp_path
) -> None:
    """关闭置顶扫描不读取置顶，但仍按已有游标增量读取同一 peer_id。"""
    sender = _lottery_sender()
    manager = _lottery_manager()
    settings = _lottery_settings(tmp_path, 60, pinned_enabled=False)
    _install_lottery_store(monkeypatch, initial_cursor=5)

    async def wait(_seconds: float, _stop_event: object, _event_bus: object = None) -> bool:
        manager.stop_event.set()
        return False

    monkeypatch.setattr("ui.send_loop._interruptible_wait", wait)
    asyncio.run(send_loop(
        sender,
        settings,
        manager,
        MagicMock(),
        lottery_scan_clock=lambda: 0.0,
    ))

    sender.get_current_pinned_messages.assert_not_awaited()
    sender.get_messages_after.assert_awaited_once_with(101, 5, 500, None)


def test_retry_does_not_send_after_stop_event_is_set() -> None:
    """停机信号在 Telegram 调用前已置位时，绝不发出普通参与消息。"""
    sender = MagicMock()
    sender.send_message = AsyncMock()
    stop_event = threading.Event()
    stop_event.set()

    result = asyncio.run(_send_with_retry(sender, "101", "不会发送", stop_event, None))

    assert result is False
    sender.send_message.assert_not_awaited()


def test_lottery_queue_and_executor_receive_configured_limits(monkeypatch, tmp_path) -> None:
    """主循环把动作间隔、配额和上海日期函数明确接线给执行组件。"""
    captured: dict[str, object] = {}

    class Queue:
        def __init__(self, **kwargs: object) -> None:
            captured["queue"] = kwargs

        async def run_once(self) -> bool:
            return False

        async def put(self, _job: object) -> bool:
            return True

    class Executor:
        def __init__(self, _sender: object, _store: object, **kwargs: object) -> None:
            captured["executor"] = kwargs

    sender = _lottery_sender()
    manager = _lottery_manager()
    settings = _lottery_settings(tmp_path, 60)
    settings.lottery_action_gap_min_seconds = 7
    settings.lottery_action_gap_max_seconds = 9
    settings.lottery_per_group_daily_limit = 2
    settings.lottery_account_daily_limit = 5
    _install_lottery_store(monkeypatch)

    async def wait(_seconds: float, _stop_event: object, _event_bus: object = None) -> bool:
        manager.stop_event.set()
        return False

    monkeypatch.setattr("ui.send_loop.LotteryQueue", Queue)
    monkeypatch.setattr("ui.send_loop.LotteryExecutor", Executor)
    monkeypatch.setattr("ui.send_loop._interruptible_wait", wait)
    asyncio.run(send_loop(sender, settings, manager, MagicMock(), lottery_scan_clock=lambda: 0.0))

    assert captured["queue"] == {"wait_min_seconds": 7, "wait_max_seconds": 9}
    assert captured["executor"]["per_group_limit"] == 2
    assert captured["executor"]["account_limit"] == 5
    assert captured["executor"]["local_date"]() == datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
