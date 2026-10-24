"""公平调度三个独立业务，所有外发汇入同一安全入口。"""

from __future__ import annotations

import asyncio
from functools import partial
import random
import sqlite3
from time import monotonic

from telethon.errors import FloodWaitError

from bewater.content import AI, Messages
from bewater.models import Action, DomainError, SHANGHAI, now
from bewater.policy import in_window


class Engine:
    """一个 asyncio 循环内运行独立群任务，外部动作全账号串行。"""

    def __init__(
        self, controller, telegram, config: dict[str, object], *, clock=now, randint=None
    ) -> None:
        """时钟和随机数可注入，测试无需等待真实日期或平台。"""
        self.control, self.telegram, self.config = controller, telegram, config
        self.store, self.events = controller.store, controller.events
        self.clock, self.randint = clock, randint or random.randint
        self.messages = Messages(controller.paths)
        self.ai = (
            AI(config, allowed=self.ai_allowed)
            if config["ai_enabled"] and config["ai_consent"]
            else None
        )
        self.identities = {}
        self.groups = {}
        self.send_lock = asyncio.Lock()
        self.cooldown_until = 0.0
        self.next_send = 0.0
        self.next_lottery = 0.0

    async def run(self) -> None:
        """先解析全部授权身份，再恢复观察和任务；不使用第二套模拟管线。"""
        from bewater.conversation import Conversation
        from bewater.lottery import Lottery

        await self.resolve_groups()
        tasks = []
        for peer, identity in self.identities.items():
            group = self.groups[peer]
            conversation = Conversation(self, identity)
            tasks.append(asyncio.create_task(self.supervise(conversation.step, peer, "chat")))
            if group["lottery"]:
                lottery = Lottery(self, identity)
                tasks.append(asyncio.create_task(self.supervise(lottery.step, peer, "lottery")))
            if group["checkin"]:
                tasks.append(
                    asyncio.create_task(
                        self.supervise(partial(self.checkins, peer), peer, "checkin")
                    )
                )
        try:
            while not self.control.stop_event.is_set():
                await asyncio.sleep(0.2)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.ai:
                await self.ai.close()

    async def resolve_groups(self) -> None:
        """重复别名需人工合并，不能叠加任务或额度。"""
        for group in self.config["groups"]:
            if not group["authorized"] or not any(group[k] for k in ("chat", "checkin", "lottery")):
                continue
            identity = await self.telegram.resolve(group["ref"])
            if identity.peer_id in self.identities:
                raise DomainError("同一群存在多个别名，请合并群组配置后重试")
            self.store.identity(identity, self.clock())
            self.identities[identity.peer_id] = identity
            self.groups[identity.peer_id] = group
            self.events.emit("group", "已确认授权群身份", identity.peer_id)

    def allowed(self, peer: int, kind: str) -> bool:
        """每次动作检查当前停用状态，启用与参数仍采用已批准启动快照。"""
        if self.control.stop_event.is_set() or self.control.state != "running":
            return False
        if monotonic() < self.cooldown_until or not in_window(self.config, self.clock()):
            return False
        identity = self.identities.get(peer)
        if identity is None or not self.groups[peer].get(kind):
            return False
        live, _ = self.control.config.load()
        group = next((g for g in live["groups"] if g["ref"] == identity.ref), None)
        if group is None or not group["authorized"] or not group[kind]:
            return False
        state = self.store.state(peer)
        if state.get("pause") in {"safety", "manual"}:
            return False
        if (
            kind == "chat"
            and state.get("pause") == "unanswered"
            and state.get("pause_day") == self.clock().date().isoformat()
        ):
            return False
        return not (kind == "lottery" and self.store.frozen(peer))

    def ai_allowed(self) -> bool:
        """撤销外发同意或关闭 AI 立即阻断下一次模型请求。"""
        if self.ai is None or self.control.stop_event.is_set():
            return False
        values, _ = self.control.config.load()
        return bool(values["ai_enabled"] and values["ai_consent"])

    async def sleep(self, seconds: float) -> None:
        """等待可随停止信号快速结束，不长期占用发送锁。"""
        until = monotonic() + max(0, seconds)
        while monotonic() < until and not self.control.stop_event.is_set():
            await asyncio.sleep(min(0.2, max(0, until - monotonic())))

    async def supervise(self, step, peer: int | None, kind: str) -> None:
        """业务错误隔离到当前任务；数据库失真则全局停止。"""
        while not self.control.stop_event.is_set():
            try:
                wait = await step()
            except asyncio.CancelledError:
                raise
            except FloodWaitError as exc:
                self.cooldown_until = max(self.cooldown_until, monotonic() + exc.seconds)
                self.events.emit(
                    "warning", f"Telegram 要求冷却 {exc.seconds} 秒，已停止新动作", peer
                )
                wait = 1
            except sqlite3.DatabaseError:
                self.events.emit("error", "数据库不可可靠读写，已停止全部外发", peer)
                self.control.stop_event.set()
                return
            except Exception:
                self.events.emit(
                    "warning",
                    f"{ {'chat': '普通参与', 'lottery': '抽奖', 'checkin': '签到'}[kind] }暂时失败，将按间隔重新检查",
                    peer,
                )
                wait = 10
            await self.sleep(wait)

    async def transmit(
        self,
        peer: int,
        action: Action,
        kind: str,
        key: str,
        *,
        original=None,
        verify=None,
        lottery_id: int | None = None,
    ) -> str:
        """所有外发的唯一入口：复核→预留→紧前门禁→调用→确认。"""
        async with self.send_lock:
            if not self.allowed(peer, kind):
                return "WAIT"
            if (
                monotonic() < self.next_send
                or kind == "lottery"
                and monotonic() < self.next_lottery
            ):
                return "WAIT"
            fresh = await verify() if verify else True
            if not fresh:
                return "SUPERSEDED"
            if not self.allowed(peer, kind):
                return "WAIT"
            current = fresh if fresh is not True else original
            limit = (
                self.config["lottery_per_group_daily_limit"]
                if kind == "lottery"
                else self.config["daily_limit"]
            )
            action_id = self.store.reserve(
                key,
                peer,
                kind,
                action.text,
                action.message_id,
                self.clock(),
                limit,
                self.config["lottery_account_daily_limit"] if kind == "lottery" else 0,
            )
            if action_id is None:
                return "BLOCKED"
            if not self.allowed(peer, kind):
                self.store.cancel_ready(action_id)
                return "WAIT"
            self.store.action_status(action_id, "CALLING")
            if lottery_id:
                self.store.lottery_result(lottery_id, "EXECUTING")
            try:
                result = await asyncio.wait_for(
                    self.telegram.perform(peer, action, current), timeout=30
                )
            except FloodWaitError as exc:
                self.cooldown_until = max(self.cooldown_until, monotonic() + exc.seconds)
                self.store.action_status(action_id, "FAILED", "Telegram 限流，未自动重试")
                return "FAILED"
            except BaseException as exc:
                self.store.action_status(action_id, "UNKNOWN", "外部结果不确定，禁止自动重试")
                if lottery_id:
                    self.store.lottery_result(
                        lottery_id, "UNKNOWN", "外部结果不确定，已冻结本群抽奖"
                    )
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if not isinstance(exc, Exception):
                    raise
                return "UNKNOWN"
            self.store.action_status(action_id, "SENT", result_id=result)
            self.next_send = monotonic() + 1
            if kind == "lottery":
                self.next_lottery = monotonic() + self.randint(
                    self.config["lottery_action_gap_min_seconds"],
                    self.config["lottery_action_gap_max_seconds"],
                )
            self.events.emit(
                "sent",
                {
                    "chat": "普通参与已发送",
                    "checkin": "每日签到已发送",
                    "lottery": "抽奖白名单动作已执行",
                }[kind],
                peer,
            )
            return "SENT"

    async def checkins(self, only_peer: int | None = None) -> float:
        """各群独立核验签到，共用当日批次；慢群不阻塞其他群。"""
        at = self.clock()
        if not in_window(self.config, at) or self.control.state != "running":
            return 1
        peers = [peer for peer, group in self.groups.items() if group["checkin"]]
        if not peers:
            return 5
        day = at.astimezone(SHANGHAI).date().isoformat()
        for peer in self.store.batch(day, peers):
            if only_peer is not None and peer != only_peer:
                continue
            if not self.allowed(peer, "checkin"):
                continue
            state = self.store.state(peer)
            if state.get("checkin_day") == day and state.get("checkin_status") in {
                "RECOVERED",
                "SENT",
                "UNKNOWN",
                "FAILED",
                "BLOCKED",
                "SUPERSEDED",
            }:
                continue
            key = f"checkin:{day}:{peer}"
            with self.store.connect() as db:
                attempted = db.execute(
                    "SELECT 1 FROM bw_action WHERE action_key=?", (key,)
                ).fetchone()
            if attempted:
                continue
            recent = await self.telegram.recent(peer, 100)
            found = next(
                (
                    m
                    for m in recent
                    if m.own
                    and m.reply_to is None
                    and m.text == "签到"
                    and m.date.astimezone(SHANGHAI).date().isoformat() == day
                ),
                None,
            )
            if found:
                self.store.audit(peer, "checkin_recovered", "发现本账号当日精确顶层签到")
                self.store.set_state(peer, checkin_day=day, checkin_status="RECOVERED")
                continue
            result = await self.transmit(peer, Action("text", 0, "签到"), "checkin", key)
            if result != "WAIT":
                self.store.set_state(peer, checkin_day=day, checkin_status=result)
        return 5
