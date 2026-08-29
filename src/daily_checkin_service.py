from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from src.daily_task_scheduler import local_date
from src.lottery_models import MessageSnapshot


class CheckinStore(Protocol):
    """每日签到所需的最小持久化接口。"""
    def claim_daily_task(self, peer_id: int, local_date: str, task_type: str) -> bool: ...
    def finish_daily_task(self, peer_id: int, local_date: str, task_type: str, status: str) -> None: ...


def is_checkin_message(message: MessageSnapshot, known_lottery_ids: set[int]) -> bool:
    """只有非回复、非抽奖的顶层精确“签到”可作为签到成功依据。"""
    return not message.is_reply and message.message_id not in known_lottery_ids and message.text == "签到"


class DailyCheckinService:
    """每日签到一次语义；签到失败不影响抽奖但门控普通参与。"""

    def __init__(self, store: CheckinStore, sender: object, clock: object, daily_limit: int,
                 state_store: object | None = None, emergency_stop: Callable[[], object] | None = None) -> None:
        self.store, self.sender, self.clock = store, sender, clock
        self.state_store = state_store or store
        self.daily_limit, self.emergency_stop = daily_limit, emergency_stop
        self._failed: set[tuple[str, int]] = set()

    def allows_normal_participation(self, peer_id: int) -> bool:
        """签到明确失败的群当天禁止普通参与。"""
        return (local_date(self.clock), peer_id) not in self._failed

    def allows_lottery(self, peer_id: int) -> bool:
        """抽奖不受签到失败门控影响。"""
        return True

    async def attempt(self, peer_id: int, group: str) -> str:
        """预留、发送、确认签到；确认失败后立即 fail-closed。"""
        today = local_date(self.clock)
        if not self.store.claim_daily_task(peer_id, today, "CHECKIN"):
            return "ALREADY_ATTEMPTED"
        reserved = False
        try:
            self.state_store.reserve_send(group, self.daily_limit)
            reserved = True
            await self.sender.send_group_text(peer_id, "签到")
            try:
                self.state_store.confirm_send(group, self.daily_limit)
            except Exception:
                self.store.finish_daily_task(peer_id, today, "CHECKIN", "CONFIRMATION_FAILED")
                if self.emergency_stop:
                    self.emergency_stop()
                return "CONFIRMATION_FAILED"
        except Exception:
            if reserved:
                try:
                    self.state_store.release_send_reservation(group)
                except Exception:
                    pass
            self._failed.add((today, peer_id))
            self.store.finish_daily_task(peer_id, today, "CHECKIN", "FAILED")
            return "FAILED"
        self.store.finish_daily_task(peer_id, today, "CHECKIN", "COMPLETED")
        return "COMPLETED"
