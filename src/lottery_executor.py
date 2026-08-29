from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from src.lottery_models import ActionType, LotteryAction, MessageSnapshot, ParticipationStatus


class LotteryActionSender(Protocol):
    """执行器使用的受控 Telegram 动作端口。"""
    async def click_callback(self, peer_id: int, message_id: int, row: int | None, column: int | None) -> str: ...
    async def send_group_text(self, peer_id: int, text: str) -> int: ...
    async def reply_to_message(self, peer_id: int, message_id: int, text: str) -> int: ...


class LotteryExecutor:
    """执行不可变计划：每步重读，预写失败永不触发 Telegram。"""
    def __init__(self, sender: LotteryActionSender, store: object, local_date: Callable[[], str] | None = None,
                 per_group_limit: int = 10, account_limit: int = 30) -> None:
        self.sender, self.store = sender, store
        self.local_date, self.per_group_limit, self.account_limit = local_date, per_group_limit, account_limit

    async def execute(self, message: MessageSnapshot, actions: tuple[LotteryAction, ...],
                      reread: Callable[[], Awaitable[MessageSnapshot | None]], lottery_id: int | None = None,
                      stop_event: object | None = None) -> str:
        """执行计划并对删除、变更、超时和负反馈安全降级。"""
        if lottery_id is not None:
            if self.local_date is not None:
                reserved = self.store.reserve_first_action(lottery_id, self.local_date(), message.peer_id,
                    self.per_group_limit, self.account_limit, [(a.kind.value, a.source_signature) for a in actions])
            else:
                reserved = self.store.prewrite_actions(lottery_id, [(a.kind.value, a.source_signature) for a in actions])
            if not reserved: return "PERSISTENCE_FAILED"
        completed = 0
        for step, action in enumerate(actions):
            if stop_event is not None and stop_event.is_set(): return "CANCELLED"
            current = await reread()
            if current is None:
                if lottery_id is not None: self.store.set_entry_status(lottery_id, ParticipationStatus.MANUAL_REQUIRED)
                return "MANUAL_REQUIRED"
            if current.text != message.text or current.buttons != message.buttons:
                if lottery_id is not None: self.store.set_action_status(lottery_id, step, "SUPERSEDED")
                return "SUPERSEDED"
            try:
                if action.kind is ActionType.CLICK_BUTTON:
                    result = await self.sender.click_callback(current.peer_id, current.message_id, action.row, action.column)
                    text = str(result).lower()
                    if "already" in text or "已参与" in str(result): result = "ALREADY_PARTICIPATED"
                    elif any(token in text for token in ("error", "fail", "denied", "invalid")):
                        if lottery_id is not None: self.store.set_entry_status(lottery_id, ParticipationStatus.MANUAL_REQUIRED)
                        return "MANUAL_REQUIRED"
                elif action.kind is ActionType.SEND_TEXT:
                    await self.sender.send_group_text(current.peer_id, action.text or "")
                else:
                    await self.sender.reply_to_message(current.peer_id, current.message_id, action.text or "")
            except (TimeoutError, ConnectionError):
                if lottery_id is not None:
                    self.store.set_action_status(lottery_id, step, "RESULT_UNKNOWN")
                    self.store.set_entry_status(lottery_id, ParticipationStatus.RESULT_UNKNOWN)
                return "RESULT_UNKNOWN"
            completed += 1
            if lottery_id is not None: self.store.set_action_status(lottery_id, step, "COMPLETED")
        if lottery_id is not None: self.store.set_entry_status(lottery_id, ParticipationStatus.PARTICIPATED)
        return "PARTICIPATED" if completed == len(actions) else "PARTIAL"
