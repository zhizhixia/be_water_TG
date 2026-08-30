from __future__ import annotations

import asyncio

import pytest

from src.daily_checkin_service import is_checkin_message
from src.daily_task_scheduler import DailyTaskScheduler
from src.instance_lock import InstanceLock
from src.lottery_ai import analyze
from src.lottery_detector import detect
from src.lottery_executor import LotteryExecutor
from src.lottery_fingerprint import base_fingerprint, normalize_text
from src.lottery_models import ActionType, ButtonSnapshot, LotteryAction, MessageSnapshot
from src.sender import PublisherTrust
from src.lottery_planner import plan
from src.lottery_queue import LotteryQueue
from src.lottery_store import LotteryStore
from src.lottery_text_policy import is_safe_fixed_text


def message(peer_id: int = 1, message_id: int = 2, text: str = "抽奖 截止 参与") -> MessageSnapshot:
    return MessageSnapshot(peer_id, message_id, text, ((ButtonSnapshot("参与", "callback", b"safe"),),))


def test_sqlite_quota_and_alert_status_are_atomic(tmp_path) -> None:
    store = LotteryStore(tmp_path / "state.db")
    entry = store.record_entry(1, 1, "fingerprint", None, "DETECTED")
    assert store.reserve_quota("2026-08-29", 1, 1, 1)
    assert not store.reserve_quota("2026-08-29", 2, 1, 1)
    store.open_alert(entry)
    assert store.is_frozen(1) is False


def test_fingerprint_planner_and_text_whitelist_fail_closed() -> None:
    assert normalize_text("Ａ\n  B") == "A B"
    assert base_fingerprint(message(1)) == base_fingerprint(message(2))
    trusted = MessageSnapshot(
        1,
        2,
        "抽奖 截止",
        ((ButtonSnapshot("参与", "callback", b"x"),),),
        publisher_trust=PublisherTrust.ADMIN,
    )
    assert plan(trusted).actions[0].kind is ActionType.CLICK_BUTTON
    assert plan(
        MessageSnapshot(
            1,
            2,
            "抽奖",
            ((ButtonSnapshot("参与", "url"),),),
            publisher_trust=PublisherTrust.ADMIN,
        )
    ).manual_reason == "unsafe_button"
    assert is_safe_fixed_text("+1")
    assert not is_safe_fixed_text("https://example.com")


@pytest.mark.asyncio
async def test_ai_fallback_queue_and_executor_do_not_call_sender_before_prewrite(tmp_path, fake_lottery_ai, fake_sender) -> None:
    assert await analyze(fake_lottery_ai, {"text": "x"}) is None
    assert not detect(MessageSnapshot(1, 2, "抽奖", ())).certain
    store = LotteryStore(tmp_path / "state.db")
    entry = store.record_entry(1, 2, "f", None, "DETECTED")
    executor = LotteryExecutor(fake_sender, store)
    result = await executor.execute(message(), (LotteryAction(ActionType.CLICK_BUTTON, "sig", 0, 0),), lambda: asyncio.sleep(0, result=message()), entry)
    assert result == "PARTICIPATED"
    assert fake_sender.calls == [("click_callback", 1, 2, 0, 0)]
    queue = LotteryQueue()
    called: list[bool] = []
    await queue.put(lambda: asyncio.sleep(0, result=called.append(True)))
    assert await queue.run_once() and called


def test_daily_batch_checkin_filter_and_instance_lock(tmp_path, fake_clock) -> None:
    store = LotteryStore(tmp_path / "state.db")
    scheduler = DailyTaskScheduler(store, fake_clock)
    assert scheduler.ensure_batch([1], False, False) == [1]
    assert scheduler.ensure_batch([1, 2], False, False) == [1]
    assert is_checkin_message(MessageSnapshot(1, 1, "签到"), set())
    assert not is_checkin_message(MessageSnapshot(1, 1, "签到", is_reply=True), set())
    lock = InstanceLock("account", str(tmp_path / "state.db"))
    assert lock.acquire()
    assert not InstanceLock("account", str(tmp_path / "state.db")).acquire()
    lock.release()


def test_lottery_api_open_and_stopped_cleanup(
    monkeypatch, app, manager, runtime_paths
) -> None:
    monkeypatch.setattr("src.config.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "x" * 32)
    monkeypatch.setenv("PHONE", "+1")
    monkeypatch.setenv("TARGET_GROUPS", "https://t.me/allowed")
    monkeypatch.setenv("MIN_INTERVAL", "10")
    monkeypatch.setenv("MAX_INTERVAL", "20")
    entry = LotteryStore(runtime_paths.state_db_path).record_entry(
        1, 2, "f", "2026-08-01", "RESULT_UNKNOWN", "<unsafe>"
    )
    client = app.test_client()
    assert client.get("/api/lotteries").get_json()["total"] == 0
    assert client.post(f"/api/lotteries/{entry}/open").status_code == 200
    manager._state = manager.state.IDLE
    assert client.post("/api/lotteries/cleanup", json={"before": "2026-08-02", "confirm": "确认清理"}).get_json()["deleted"]["lottery_entry"] == 1
