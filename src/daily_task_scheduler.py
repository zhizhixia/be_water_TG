from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def local_date(clock: object) -> str:
    """从注入时钟取得上海自然日。"""
    now = clock.now() if hasattr(clock, "now") else datetime.now(ZoneInfo("Asia/Shanghai"))
    return now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def should_create_batch(schedule_enabled: bool, in_window: bool) -> bool:
    """有计划只在窗口内建批次；无计划立即可建。"""
    return not schedule_enabled or in_window


class DailyTaskScheduler:
    """用注入时钟创建不可变的上海日批次。"""

    def __init__(self, store: object, clock: object) -> None:
        self.store = store
        self.clock = clock

    def ensure_batch(self, peer_ids: list[int], schedule_enabled: bool, in_window: bool) -> list[int]:
        """仅在允许窗口创建批次，已有批次直接返回原快照。"""
        today = local_date(self.clock)
        existing = self.store.daily_batch_groups(today)
        if existing:
            return existing
        if not should_create_batch(schedule_enabled, in_window):
            return []
        self.store.create_daily_batch(today, peer_ids)
        return self.store.daily_batch_groups(today)

    def recover_unfinished(self, task_types: set[str]) -> dict[int, set[str]]:
        """返回当天快照中重启后仍应补做的任务，不把新增群并入批次。

        仓储应提供 ``daily_task_status(peer_id, local_date, task_type)``；为
        兼容测试隔离 fake，也支持其公开的 ``finished`` 集合。
        """
        today = local_date(self.clock)
        unfinished: dict[int, set[str]] = {}
        for peer_id in self.store.daily_batch_groups(today):
            pending: set[str] = set()
            for task_type in task_types:
                if hasattr(self.store, "daily_task_status"):
                    status = self.store.daily_task_status(peer_id, today, task_type)
                    done = status in {"COMPLETED", "FAILED"}
                else:
                    done = (today, peer_id, task_type) in getattr(self.store, "finished", set())
                if not done:
                    pending.add(task_type)
            if pending:
                unfinished[peer_id] = pending
        return unfinished
