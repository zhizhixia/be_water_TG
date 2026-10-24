"""抽奖发现、断点扫描、待办与受限执行的真实业务路径。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import re
from time import monotonic

from bewater.models import Identity, Message
from bewater.policy import lottery_plan, signature


class Lottery:
    """每群扫描独立，等待中的计划不占用普通聊天调度。"""

    def __init__(self, engine, identity: Identity) -> None:
        """保存当前扫描周期和可恢复计划队列。"""
        self.engine, self.identity = engine, identity
        self.peer = identity.peer_id
        self.next_scan = 0.0
        self.scanning = False
        self.pending = []

    async def step(self) -> float:
        """每次处理一个计划或一页消息，让出事件循环给其他任务。"""
        engine = self.engine
        if not engine.allowed(self.peer, "lottery"):
            return 2
        if not self.pending:
            await self.recover_plans()
        if self.pending and monotonic() >= max(engine.next_send, engine.next_lottery):
            record_id, original = self.pending[0]
            result = await self.execute(record_id, original)
            if result != "WAIT":
                self.pending.pop(0)
        if monotonic() < self.next_scan and not self.scanning:
            return 2
        try:
            await self.scan_page()
        except Exception:
            self.scanning = False
            self.next_scan = monotonic() + engine.config["lottery_scan_interval_minutes"] * 60
            engine.store.scan_failure(self.peer, engine.clock())
            raise
        return 2

    async def scan_page(self) -> None:
        """正序分页不漏消息；无游标启动只建立普通历史基线。"""
        engine, at = self.engine, self.engine.clock()
        state = engine.store.scan(self.peer)
        cursor = state["cursor"]
        if not self.scanning:
            self.next_scan = monotonic() + engine.config["lottery_scan_interval_minutes"] * 60
            engine.store.set_state(
                self.peer,
                scan_next=(
                    at + timedelta(minutes=engine.config["lottery_scan_interval_minutes"])
                ).isoformat(),
            )
            if engine.config["daily_pinned_lottery_scan_enabled"]:
                pinned = await engine.telegram.pinned(self.peer)
                for message in pinned:
                    if message.date.date() == at.date() and message.pinned:
                        await self.discover(replace(message, source="pinned_today"))
            if cursor is None:
                latest = await engine.telegram.latest(self.peer)
                engine.store.checkpoint(self.peer, latest, at)
                engine.events.emit("scan", "已建立扫描基线，不回溯普通历史", self.peer)
                return
            self.scanning = True
        messages = await engine.telegram.after(self.peer, int(cursor), 100)
        cutoff = at - timedelta(hours=1)
        for message in messages:
            if cutoff <= message.date <= at:
                await self.discover(replace(message, source="incremental_recent"))
        engine.store.checkpoint(self.peer, messages[-1].id if messages else int(cursor), at)
        self.scanning = len(messages) == 100
        engine.events.emit(
            "scan", "已处理一页增量消息" if self.scanning else "本轮扫描完成", self.peer
        )

    async def discover(self, message: Message) -> None:
        """识别只是发现，任何不明确条件转人工，不直接产生外部动作。"""
        engine, at = self.engine, self.engine.clock()
        if not message.text or message.own:
            return
        obvious = bool(re.search(r"抽奖|奖品|giveaway", message.text, re.I))
        potential = bool(re.search(r"奖|赠|福利|prize|winning", message.text, re.I))
        analysis = None
        if not obvious and potential and engine.ai_allowed():
            analysis = await engine.ai.lottery(message)
        if not obvious and not (analysis and analysis["is_lottery"]):
            return
        if re.search(r"抽奖结束|已结束|开奖结束", message.text):
            return
        fingerprint = signature(message)
        trust = await engine.telegram.trust(self.peer, message.sender_id)
        plan = lottery_plan(message, trust, at)
        if analysis and analysis["required_actions"] not in [
            ["参与"],
            ["点击参与"],
            ["点击参与按钮"],
        ]:
            from bewater.policy import LotteryPlan

            plan = LotteryPlan(None, "AI 发现额外或未知条件，需人工核查", plan.deadline)
        end = plan.deadline.isoformat() if plan.deadline else None
        if engine.store.find_lottery(self.peer, message.id, fingerprint, end, at) is not None:
            return
        status = (
            "PLANNED" if plan.action else ("EXPIRED" if plan.reason == "已过截止时间" else "MANUAL")
        )
        record_id = engine.store.lottery(
            self.peer,
            message.id,
            fingerprint,
            end,
            at,
            status,
            plan.reason,
            "明确抽奖" if obvious else "疑似抽奖",
            source=message.source,
        )
        if plan.action:
            self.pending.append((record_id, message))
        engine.events.emit(
            "lottery", "抽奖已进入安全复核" if plan.action else "发现抽奖，需人工查看", self.peer
        )

    async def recover_plans(self) -> None:
        """重启后只恢复尚未进入外部调用的已发现计划。"""
        with self.engine.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM bw_lottery WHERE peer=? AND status='PLANNED' ORDER BY id LIMIT 10",
                (self.peer,),
            ).fetchall()
        for row in rows:
            current = await self.engine.telegram.message(self.peer, row["message_id"])
            if current is None or signature(current) != row["fingerprint"]:
                self.engine.store.lottery_result(row["id"], "MANUAL", "原消息已删除或变化")
                continue
            source = (
                "pinned_today"
                if current.pinned and current.date.date() == self.engine.clock().date()
                else "incremental_recent"
            )
            self.pending.append((row["id"], replace(current, source=source)))

    async def execute(self, record_id: int, original: Message) -> str:
        """锁内重读和权限重查后重新规划，旧签名变化绝不继续点击。"""
        engine = self.engine
        trust = await engine.telegram.trust(self.peer, original.sender_id)
        plan = lottery_plan(original, trust, engine.clock())
        if not plan.action:
            engine.store.lottery_result(record_id, "MANUAL", plan.reason)
            return "MANUAL"

        async def verify():
            """每次实际外发前重新检查原文、发布者、截止与完整计划。"""
            current = await engine.telegram.message(self.peer, original.id)
            if (
                current is None
                or signature(current) != signature(original)
                or current.sender_id != original.sender_id
            ):
                return False
            current = replace(current, source=original.source)
            role = await engine.telegram.trust(self.peer, current.sender_id)
            fresh = lottery_plan(current, role, engine.clock())
            return current if fresh.action == plan.action and fresh.action is not None else False

        result = await engine.transmit(
            self.peer,
            plan.action,
            "lottery",
            f"lottery:{self.peer}:{original.id}",
            original=original,
            verify=verify,
            lottery_id=record_id,
        )
        if result == "WAIT":
            return result
        status = {
            "SENT": "EXECUTED",
            "UNKNOWN": "UNKNOWN",
            "SUPERSEDED": "MANUAL",
            "BLOCKED": "MANUAL",
            "FAILED": "MANUAL",
        }[result]
        engine.store.lottery_result(
            record_id,
            status,
            {
                "SENT": "白名单动作已执行，不代表中奖",
                "UNKNOWN": "结果不确定，禁止重试",
                "SUPERSEDED": "执行前原消息或条件变化",
                "BLOCKED": "已处理、冻结或达到配额",
                "FAILED": "平台拒绝执行",
            }[result],
        )
        engine.store.audit(self.peer, "lottery_result", status)
        return result
