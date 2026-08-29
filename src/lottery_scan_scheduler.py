from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class ScanStore(Protocol):
    """扫描调度需要的独立游标持久化接口。"""
    def scan_state(self, peer_id: int) -> dict[str, object]: ...
    def commit_scan_batch(self, peer_id: int, cursor_message_id: int, messages: list[object]) -> None: ...


class ScanSource(Protocol):
    """受控 Telegram 扫描接口，不暴露 Telethon 对象。"""
    async def latest_message_id(self, peer_id: int) -> int | None: ...
    async def messages_after(self, peer_id: int, cursor: int, limit: int, min_occurred_at: datetime | None) -> list[object]: ...


def scan_due(last_attempt_at: datetime | None, interval_minutes: int, now: datetime, flood_until: datetime | None = None) -> bool:
    """失败完成后等待完整间隔，且较长 FloodWait 优先。"""
    due = last_attempt_at is None or now >= last_attempt_at + timedelta(minutes=interval_minutes)
    return due and (flood_until is None or now >= flood_until)


def recent_cutoff(now: datetime) -> datetime:
    """返回上海时区最近一小时边界。"""
    return now.astimezone(ZoneInfo("Asia/Shanghai")) - timedelta(hours=1)


class LotteryScanScheduler:
    """冷启动置基线、增量单独游标且批次成功后才提交检查点。"""
    def __init__(self, store: ScanStore, source: ScanSource, clock: object, interval_minutes: int = 60) -> None:
        self.store, self.source, self.clock, self.interval_minutes = store, source, clock, interval_minutes

    async def scan_incremental(self, peer_id: int, process: Callable[[list[object]], object]) -> list[object]:
        """扫描最多 500 条；处理失败时不提交本批游标。"""
        now = self.clock.now()
        state = self.store.scan_state(peer_id)
        cursor = state.get("cursor_message_id")
        if cursor is None:
            latest = await self.source.latest_message_id(peer_id)
            if latest is not None:
                self.store.commit_scan_batch(peer_id, latest, [])
            return []
        messages = await self.source.messages_after(peer_id, int(cursor), 500, recent_cutoff(now))
        batch = list(messages[:500])
        result = process(batch)
        if inspect.isawaitable(result):
            await result
        if batch:
            highest = max(int(getattr(item, "message_id", item)) for item in batch)
            self.store.commit_scan_batch(peer_id, highest, batch)
        return batch
