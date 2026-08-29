from datetime import datetime
from zoneinfo import ZoneInfo

from src.daily_task_scheduler import DailyTaskScheduler


class Clock:
    def __init__(self, now): self.value = now
    def now(self): return self.value


class Store:
    def __init__(self): self.batches = {}
    def daily_batch_groups(self, day): return self.batches.get(day, [])
    def create_daily_batch(self, day, peers):
        if day not in self.batches:
            self.batches[day] = list(peers)
            return True
        return False


def test_planned_batch_only_starts_on_first_window_and_keeps_snapshot():
    clock = Clock(datetime(2026, 8, 29, 8, tzinfo=ZoneInfo("Asia/Shanghai")))
    store = Store()
    scheduler = DailyTaskScheduler(store, clock)

    assert scheduler.ensure_batch([1, 2], schedule_enabled=True, in_window=False) == []
    assert scheduler.ensure_batch([1, 2], schedule_enabled=True, in_window=True) == [1, 2]
    assert scheduler.ensure_batch([1, 2, 3], schedule_enabled=True, in_window=True) == [1, 2]


def test_unplanned_start_and_cross_midnight_create_separate_shanghai_batches():
    clock = Clock(datetime(2026, 8, 29, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")))
    store = Store()
    scheduler = DailyTaskScheduler(store, clock)
    assert scheduler.ensure_batch([9], False, False) == [9]
    clock.value = datetime(2026, 8, 30, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert scheduler.ensure_batch([9], False, False) == [9]
    assert set(store.batches) == {"2026-08-29", "2026-08-30"}


def test_restart_returns_only_unfinished_snapshot_tasks():
    clock = Clock(datetime(2026, 8, 29, 9, tzinfo=ZoneInfo("Asia/Shanghai")))
    store = Store()
    scheduler = DailyTaskScheduler(store, clock)
    scheduler.ensure_batch([1, 2, 3], False, False)
    store.finished = {("2026-08-29", 1, "CHECKIN"), ("2026-08-29", 2, "PINNED_SCAN")}
    assert scheduler.recover_unfinished({"CHECKIN", "PINNED_SCAN"}) == {1: {"PINNED_SCAN"}, 2: {"CHECKIN"}, 3: {"CHECKIN", "PINNED_SCAN"}}
