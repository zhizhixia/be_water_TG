from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

import pytest

from src.daily_checkin_service import DailyCheckinService


class Clock:
    def now(self): return datetime(2026, 8, 29, tzinfo=ZoneInfo("Asia/Shanghai"))
class Store:
    def __init__(self): self.claimed = set(); self.finished = []
    def claim_daily_task(self, peer, day, kind):
        key = peer, day, kind
        if key in self.claimed: return False
        self.claimed.add(key); return True
    def finish_daily_task(self, *args): self.finished.append(args)
class State:
    def reserve_send(self, *args): pass
    def confirm_send(self, *args): raise OSError("disk unavailable")
    def release_send_reservation(self, *args): pass
class Sender:
    def __init__(self): self.calls = []
    async def send_group_text(self, peer, text): self.calls.append((peer, text)); return 1

@pytest.mark.asyncio
async def test_confirmation_failure_requests_fail_closed_emergency_stop_after_send():
    sender = Sender(); stopped = []
    service = DailyCheckinService(Store(), sender, Clock(), 3, State(), emergency_stop=lambda: stopped.append(True))
    assert await service.attempt(1, "1") == "CONFIRMATION_FAILED"
    assert sender.calls == [(1, "签到")]
    assert stopped == [True]

@pytest.mark.asyncio
async def test_attempt_stops_before_sending_when_stop_event_is_already_set():
    """停机已开始时，释放额度预留且不把签到标记为完成。"""
    class TrackingState:
        def __init__(self):
            self.reserved = []
            self.released = []

        def reserve_send(self, *args):
            self.reserved.append(args)

        def confirm_send(self, *args):
            raise AssertionError("停机时不应确认发送")

        def release_send_reservation(self, *args):
            self.released.append(args)

    store = Store()
    sender = Sender()
    state = TrackingState()
    stop_event = Event()
    stop_event.set()
    service = DailyCheckinService(store, sender, Clock(), 3, state)

    assert await service.attempt(1, "1", stop_event=stop_event) == "STOPPED"
    assert sender.calls == []
    assert state.reserved == [("1", 3)]
    assert state.released == [("1",)]
    assert store.finished == [(1, "2026-08-29", "CHECKIN", "STOPPED")]


@pytest.mark.asyncio
async def test_attempt_stops_if_event_is_set_after_reserving_and_before_send():
    """在发送调用紧前发生停机时，绝不调用 Telegram 且释放额度。"""
    class StopBeforeSendState:
        def __init__(self, stop_event):
            self.stop_event = stop_event
            self.released = []

        def reserve_send(self, *args):
            self.stop_event.set()

        def confirm_send(self, *args):
            raise AssertionError("停机时不应确认发送")

        def release_send_reservation(self, *args):
            self.released.append(args)

    store = Store()
    sender = Sender()
    stop_event = Event()
    state = StopBeforeSendState(stop_event)
    service = DailyCheckinService(store, sender, Clock(), 3, state)

    assert await service.attempt(1, "1", stop_event=stop_event) == "STOPPED"
    assert sender.calls == []
    assert state.released == [("1",)]
    assert store.finished == [(1, "2026-08-29", "CHECKIN", "STOPPED")]


@pytest.mark.asyncio
async def test_failed_checkin_is_once_only_and_exposes_normal_participation_gate():
    class BadSender(Sender):
        async def send_group_text(self, peer, text): raise ConnectionError()
    service = DailyCheckinService(Store(), BadSender(), Clock(), 3, State())
    assert await service.attempt(1, "1") == "FAILED"
    assert await service.attempt(1, "1") == "ALREADY_ATTEMPTED"
    assert service.allows_normal_participation(1) is False
    assert service.allows_lottery(1) is True
