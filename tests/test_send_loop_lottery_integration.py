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
from src.lottery_store import LotteryStore
from src.state_store import StateStore
from ui.send_loop import _in_schedule, _send_with_retry, send_loop


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
    LotteryStore(settings.state_db_path).commit_scan_batch(101, 5, [])

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

        async def run_once(self, *, stop_event: object) -> bool:
            captured["queue_stop_event"] = stop_event
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
    assert captured["queue_stop_event"] is manager.stop_event


def test_lottery_scan_uses_real_sqlite_store_for_baseline_and_incremental_cursor(
    monkeypatch, tmp_path
) -> None:
    """冷启动只建基线，下一轮把真实消息批次游标提交到 SQLite。"""
    sender = _lottery_sender()
    manager = _lottery_manager()
    settings = _lottery_settings(tmp_path, 0, pinned_enabled=False)
    message = MessageSnapshot(101, 12, "普通消息", occurred_at="2026-08-29T09:00:00+08:00")
    sender.get_latest_message_id.return_value = 10
    sender.get_messages_after.return_value = [message]
    waits = 0

    async def wait(_seconds: float, _stop_event: object, _event_bus: object = None) -> bool:
        nonlocal waits
        waits += 1
        if waits == 2:
            manager.stop_event.set()
        return not manager.stop_event.is_set()

    monkeypatch.setattr("ui.send_loop._interruptible_wait", wait)
    asyncio.run(send_loop(sender, settings, manager, MagicMock(), lottery_scan_clock=lambda: 0.0))

    assert LotteryStore(settings.state_db_path).scan_state(101)["cursor_message_id"] == 12
    sender.get_latest_message_id.assert_awaited_once_with(101)
    sender.get_messages_after.assert_awaited_once_with(101, 10, 500, None)


def test_checkin_daily_snapshot_is_intersected_with_current_target_peers(monkeypatch, tmp_path) -> None:
    """重启后已移除的群即使仍在当日快照也不得外发签到。"""
    settings = Settings(
        api_id=1, api_hash="x" * 32, phone="+1", target_groups=["https://t.me/allowed"],
        state_db_path=str(tmp_path / "state.db"), lottery_enabled=False, daily_checkin_enabled=True,
    )
    now = datetime(2026, 8, 29, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    LotteryStore(settings.state_db_path).create_daily_batch(now.date().isoformat(), [101, 202])
    sender = _lottery_sender()
    manager = _lottery_manager()
    sender.send_group_text = AsyncMock(return_value=1)

    async def wait(_seconds: float, _stop_event: object, _event_bus: object = None) -> bool:
        manager.stop_event.set()
        return False

    monkeypatch.setattr("ui.send_loop._interruptible_wait", wait)
    asyncio.run(send_loop(sender, settings, manager, MagicMock(), shanghai_now=lambda: now))

    assert [call.args[0] for call in sender.send_group_text.await_args_list] == [101]


def test_schedule_converts_injected_datetime_to_asia_shanghai() -> None:
    """排程判断将可注入 UTC 时间转换为固定上海时区。"""
    settings = Settings(api_id=1, api_hash="x" * 32, phone="+1", target_groups=["https://t.me/allowed"])
    settings.schedule_enabled = True
    settings.schedule_morning_start = "08:00"
    settings.schedule_morning_end = "09:00"
    settings.schedule_afternoon_start = "14:00"
    settings.schedule_afternoon_end = "15:00"

    assert _in_schedule(settings, datetime(2026, 8, 29, 0, 30, tzinfo=ZoneInfo("UTC"))) is True


def test_lottery_stop_clears_queue_and_events_only_expose_safe_identifiers(
    monkeypatch, tmp_path
) -> None:
    """停机清空队列，管理器事件只携带稳定 ID 与固定状态码。"""
    stopped: list[bool] = []

    class Queue:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run_once(self, *, stop_event: threading.Event) -> bool:
            stop_event.set()
            return False

        async def stop(self) -> None:
            stopped.append(True)

    sender = _lottery_sender()
    manager = _lottery_manager()
    manager.emit_lottery_event = AsyncMock()
    settings = _lottery_settings(tmp_path, 60, pinned_enabled=False)
    monkeypatch.setattr("ui.send_loop.LotteryQueue", Queue)

    asyncio.run(send_loop(sender, settings, manager, MagicMock(), lottery_scan_clock=lambda: 0.0))

    assert stopped == [True]
    assert manager.emit_lottery_event.await_args_list
    for call in manager.emit_lottery_event.await_args_list:
        event_type, payload = call.args
        assert event_type in {"lottery_alert", "lottery_updated", "scan_health", "checkin_health"}
        assert set(payload) <= {"peer_id", "lottery_id", "reason", "status"}
