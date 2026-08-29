from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.lottery_scan_scheduler import LotteryScanScheduler


class Clock:
    def __init__(self, value): self.value = value
    def now(self): return self.value
class Store:
    def __init__(self): self.cursor = 10; self.saved = []
    def scan_state(self, peer): return {"cursor_message_id": self.cursor, "last_attempt_at": None, "flood_until": None}
    def commit_scan_batch(self, peer, cursor, messages): self.saved.append((peer, cursor, list(messages))); self.cursor = cursor
class Source:
    async def latest_message_id(self, peer): return 99
    async def messages_after(self, peer, cursor, limit, min_occurred_at):
        self.args = peer, cursor, limit, min_occurred_at
        return list(range(11, 512))


@pytest.mark.asyncio
async def test_incremental_scan_caps_batch_and_only_commits_cursor_after_processor_success():
    now = datetime(2026, 8, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai")); source = Source(); store = Store()
    scheduler = LotteryScanScheduler(store, source, Clock(now), interval_minutes=60)
    processed = []
    await scheduler.scan_incremental(1, processed.extend)
    assert source.args[2] == 500
    assert processed == list(range(11, 511))
    assert store.saved[-1][1] == 510


@pytest.mark.asyncio
async def test_cold_start_sets_baseline_without_scanning_history():
    now = datetime(2026, 8, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai")); source = Source(); store = Store(); store.cursor = None
    scheduler = LotteryScanScheduler(store, source, Clock(now), interval_minutes=60)
    assert await scheduler.scan_incremental(1, lambda _: None) == []
    assert store.saved[-1][1] == 99
