from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from src.lottery_models import ActionType, LotteryAction, MessageSnapshot, ParticipationStatus
from src.lottery_planner import Plan, parse_verified_deadline, plan
from src.sender import PublisherTrust


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
                      stop_event: object | None = None,
                      trust_resolver: Callable[[int, int | None], Awaitable[PublisherTrust]] | None = None,
                      replan: Callable[[MessageSnapshot], Plan] = plan,
                      now: Callable[[], datetime] | None = None,
                      can_execute: Callable[[], bool] | None = None) -> str:
        """执行计划并对删除、变更、超时和负反馈安全降级。"""
        if not actions:
            if lottery_id is not None:
                self.store.set_entry_status(lottery_id, ParticipationStatus.MANUAL_REQUIRED)
            return "MANUAL_REQUIRED"
        if self._blocked(stop_event, can_execute):
            return "CANCELLED"
        if getattr(self.store, "is_frozen", lambda _peer: False)(message.peer_id):
            return "FROZEN"
        completed = 0
        for step, action in enumerate(actions):
            if self._blocked(stop_event, can_execute): return "CANCELLED"
            reviewed = await self._review(message, actions, reread, trust_resolver, replan, now)
            if isinstance(reviewed, str):
                if lottery_id is not None:
                    status = ParticipationStatus.EXPIRED if reviewed == "EXPIRED" else ParticipationStatus.MANUAL_REQUIRED
                    self.store.set_entry_status(lottery_id, status)
                    if reviewed == "SUPERSEDED":
                        self.store.set_action_status(lottery_id, step, "SUPERSEDED")
                return reviewed
            current, current_actions = reviewed
            action = current_actions[step]
            if step == 0 and lottery_id is not None:
                if self._blocked(stop_event, can_execute):
                    return "CANCELLED"
                serialized = [(item.kind.value, item.source_signature) for item in actions]
                if self.local_date is not None:
                    reserved = self.store.reserve_first_action(
                        lottery_id, self.local_date(), message.peer_id,
                        self.per_group_limit, self.account_limit, serialized,
                    )
                else:
                    reserved = self.store.prewrite_actions(lottery_id, serialized)
                if not reserved:
                    return "PERSISTENCE_FAILED"
            if self._blocked(stop_event, can_execute): return "CANCELLED"
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

    @staticmethod
    def _blocked(stop_event: object | None, can_execute: Callable[[], bool] | None) -> bool:
        """在每个外部调用紧前统一检查停止、暂停和时段门禁。"""
        return bool(
            stop_event is not None and stop_event.is_set()
            or can_execute is not None and not can_execute()
        )

    async def _review(self, original: MessageSnapshot, actions: tuple[LotteryAction, ...],
                      reread: Callable[[], Awaitable[MessageSnapshot | None]],
                      trust_resolver: Callable[[int, int | None], Awaitable[PublisherTrust]] | None,
                      replan: Callable[[MessageSnapshot], Plan], now: Callable[[], datetime] | None
                      ) -> tuple[MessageSnapshot, tuple[LotteryAction, ...]] | str:
        """重读并复核消息、发布者、截止和完整白名单计划。"""
        current = await reread()
        if current is None:
            return "MANUAL_REQUIRED"
        if current.text != original.text or current.buttons != original.buttons:
            return "SUPERSEDED"
        if trust_resolver is not None:
            trust = trust_resolver(current.peer_id, current.sender_id)
            if inspect.isawaitable(trust):
                trust = await trust
            current = replace(current, publisher_trust=trust)
        else:
            current = replace(current, publisher_trust=original.publisher_trust)
        current = replace(
            current,
            deadline=original.deadline,
            deadline_claimed=original.deadline_claimed,
            deadline_required=original.deadline_required,
            required_actions=original.required_actions,
            ai_reviewed=original.ai_reviewed,
        )
        if current.deadline:
            deadline = parse_verified_deadline(current.deadline)
            if deadline is None or now is None:
                return "MANUAL_REQUIRED"
            current_now = now()
            if current_now.tzinfo is None or current_now.utcoffset() is None:
                return "MANUAL_REQUIRED"
            if current_now >= deadline:
                return "EXPIRED"
        fresh = replan(current)
        if fresh.manual_reason or fresh.actions != actions:
            return "SUPERSEDED" if not fresh.manual_reason else "MANUAL_REQUIRED"
        return current, fresh.actions
