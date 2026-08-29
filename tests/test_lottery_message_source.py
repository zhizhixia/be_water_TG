from datetime import datetime
from zoneinfo import ZoneInfo
from src.lottery_message_source import current_day_pinned
from src.lottery_models import MessageSnapshot


def test_pinned_source_keeps_only_current_shanghai_day_and_degrades_to_latest():
    today = "2026-08-29"
    old = MessageSnapshot(1, 1, "old", occurred_at="2026-08-28T23:59:00+08:00", pinned=True)
    new = MessageSnapshot(1, 2, "new", occurred_at="2026-08-29T00:01:00+08:00", pinned=True)
    assert current_day_pinned([old, new], today, complete_enumeration=False) == [new]
