"""自然参与：候选属于当前话题，而不是周期群发计时器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from time import monotonic

from bewater.models import Action, Identity, Message, SHANGHAI
from bewater.policy import ADMIN_WARNING_TERMS, classify, safety


@dataclass
class Candidate:
    """仅内存保存，进程重启不会补发过时内容。"""

    trigger: str
    context_text: str
    text: str
    due: float


def same_topic(left: str, right: str) -> bool:
    """无额外模型调用时，用共同实词/汉字片段保守判断话题连续。"""

    def terms(value: str) -> set[str]:
        """提取中文双字片段与英文单词。"""
        words = set(re.findall(r"[A-Za-z]{3,}", value.casefold()))
        for run in re.findall(r"[\u4e00-\u9fff]+", value):
            words.update(run[i : i + 2] for i in range(len(run) - 1))
        return words - {"怎么", "如何", "这个", "那个", "有没有", "我们", "你们"}

    return bool(terms(left) & terms(right))


class Conversation:
    """每群独立观察，发送仍受全账号统一门禁。"""

    def __init__(self, engine, identity: Identity) -> None:
        """记录群身份与候选，不持有独立 Telegram 客户端。"""
        self.engine, self.identity = engine, identity
        self.peer = identity.peer_id
        self.initialized = False
        self.pending: Candidate | None = None
        self.context: list[Message] = []
        self.baseline_at = engine.clock()

    async def step(self) -> float:
        """先持续观察安全信号，再生成或发送当前候选。"""
        engine, peer = self.engine, self.peer
        state = engine.store.state(peer)
        size = engine.config["ai_context_count"] + 10
        if not self.initialized:
            self.context = await engine.telegram.recent(peer, size)
            cursor = max((m.id for m in self.context), default=0)
            engine.store.set_state(
                peer, chat_cursor=cursor, last_activity=engine.clock().isoformat()
            )
            self.initialized = True
            return 30
        messages = await engine.telegram.after(peer, int(state.get("chat_cursor", 0)), 100)
        self.context = (self.context + messages)[-size:]
        context = self.context
        await self.observe(messages, context)
        if not engine.allowed(peer, "chat"):
            self.pending = None
            return self.poll_delay()
        if self.pending:
            await self.refresh_pending(messages, context)
            if self.pending and monotonic() >= self.pending.due:
                await self.send_pending()
            if self.pending:
                return self.poll_delay()
        incoming = [m for m in messages if not m.own and m.text]
        if incoming:
            await self.consider(incoming[-1], context, False)
        elif self.is_idle(context):
            await self.consider(context[-1], context, True)
        return self.poll_delay()

    def poll_delay(self) -> float:
        """普通观察每30秒一次，候选临近到期时缩短，不持续双拉历史。"""
        return max(2, min(30, self.pending.due - monotonic())) if self.pending else 30

    async def observe(self, messages: list[Message], context: list[Message]) -> None:
        """权限查询只针对警告发送者；推进游标不丢失本轮消息。"""
        engine, peer = self.engine, self.peer
        state = engine.store.state(peer)
        if (
            state.get("pause") == "unanswered"
            and state.get("pause_day") != engine.clock().date().isoformat()
        ):
            engine.store.set_state(peer, pause="", pause_reason="")
        own_ids = {m.id for m in context if m.own}
        if state.get("last_outbound_id"):
            own_ids.add(state["last_outbound_id"])
        candidates = {
            m.sender_id
            for m in messages
            if not m.own and any(word in m.text for word in ADMIN_WARNING_TERMS)
        }
        admins = set()
        for sender_id in candidates:
            if await engine.telegram.trust(peer, sender_id) in {"owner", "admin"}:
                admins.add(sender_id)
        reason = safety(messages, own_ids, admins)
        if reason:
            engine.store.set_state(peer, pause="safety", pause_reason=reason)
            engine.store.audit(peer, "safety_pause", reason)
            engine.events.emit("warning", reason, peer)
            self.pending = None
        incoming = [m for m in messages if not m.own and m.text]
        changes = {}
        if messages:
            changes["chat_cursor"] = max(m.id for m in messages)
        if incoming:
            changes["last_activity"] = incoming[-1].date.isoformat()
        if changes:
            engine.store.set_state(peer, **changes)
        self.check_unanswered()

    def check_unanswered(self) -> None:
        """以发送后的完整冷场窗口判断，不检查旧文本是否仍在上下文。"""
        engine, peer = self.engine, self.peer
        state = engine.store.state(peer)
        outbound = state.get("last_outbound")
        if not outbound or state.get("pause") in {"safety", "manual", "unanswered"}:
            return
        last = datetime.fromisoformat(outbound)
        if last.astimezone(SHANGHAI).date() != engine.clock().astimezone(SHANGHAI).date():
            return
        activity = datetime.fromisoformat(state.get("last_activity", outbound))
        if activity > last:
            return
        if engine.clock() >= last + timedelta(minutes=engine.config["idle_threshold_minutes"] * 2):
            engine.store.set_state(
                peer,
                pause="unanswered",
                pause_day=engine.clock().date().isoformat(),
                pause_reason="两个完整冷场窗口无群友回应，暂停到次日",
            )
            engine.events.emit("warning", "连续无人回应，普通参与暂停到次日", peer)

    def is_idle(self, context: list[Message]) -> bool:
        """本人是最新发言者时不主动接连说话。"""
        if not context or context[-1].own:
            return False
        state = self.engine.store.state(self.peer)
        activity = datetime.fromisoformat(state.get("last_activity", self.baseline_at.isoformat()))
        return self.engine.clock() >= max(activity, self.baseline_at) + timedelta(
            minutes=self.engine.config["idle_threshold_minutes"]
        )

    async def consider(self, message: Message, context: list[Message], idle: bool) -> None:
        """一个新消息或冷场事件只进行一次概率决策。"""
        engine = self.engine
        trigger = ("idle:" if idle else "message:") + str(message.id)
        if not engine.store.decide_once(self.peer, trigger):
            return
        kind = (
            "discussion"
            if idle
            else (
                await engine.ai.classify(message.text)
                if engine.ai_allowed()
                else classify(message.text)
            )
        )
        probability = (
            engine.config["question_reply_pct"]
            if kind == "question"
            else engine.config["discussion_reply_pct"]
        )
        if kind == "irrelevant" or engine.randint(1, 100) > probability:
            engine.store.audit(self.peer, "skip", "内容不相关或未命中参与概率")
            engine.store.set_state(self.peer, last_decision="内容不相关或未命中参与概率")
            return
        text = await self.generate(context)
        if not text:
            engine.store.audit(self.peer, "skip", "没有相关且不重复的内容")
            engine.store.set_state(self.peer, last_decision="没有相关且不重复的内容")
            return
        delay = engine.randint(engine.config["reply_delay_min"], engine.config["reply_delay_max"])
        self.pending = Candidate(trigger, message.text, text, monotonic() + delay)
        engine.store.set_state(self.peer, last_decision=f"已形成候选，等待{delay}秒复核")
        engine.events.emit("decision", f"形成候选，等待 {delay} 秒后复核", self.peer)

    async def generate(self, context: list[Message]) -> str | None:
        """只在 AI 不可用时兜底，模型主动跳过不强发 TXT。"""
        engine = self.engine
        history = engine.store.recent_texts(self.peer)
        if engine.ai_allowed():
            try:
                return await engine.ai.generate(context, history)
            except Exception:
                engine.events.emit("warning", "AI 暂时不可用，尝试受控消息库", self.peer)
        return engine.messages.choose(engine.groups[self.peer]["messages"], history, engine.randint)

    async def refresh_pending(self, messages: list[Message], context: list[Message]) -> None:
        """新话题取消旧候选，延续话题则重新生成且不重复抽概率。"""
        incoming = [m for m in messages if not m.own and m.text]
        if not incoming or not self.pending:
            return
        latest = incoming[-1]
        if not same_topic(self.pending.context_text, latest.text):
            self.pending = None
            self.engine.events.emit("decision", "话题变化，取消过时候选并重新评估新消息", self.peer)
            return
        text = await self.generate(context)
        if text:
            self.pending.text = text
            self.pending.context_text = latest.text
        else:
            self.pending = None

    async def verify(self) -> bool:
        """发送锁内重读新增消息；未观察的新活动交回下一轮而不是丢弃。"""
        state = self.engine.store.state(self.peer)
        messages = await self.engine.telegram.after(
            self.peer, int(state.get("chat_cursor", 0)), 100
        )
        if messages:
            return False
        return self.pending is not None and self.engine.allowed(self.peer, "chat")

    async def send_pending(self) -> None:
        """发送成功后才提交上下文历史，未知结果保留额度并明确提示。"""
        pending = self.pending
        if not pending:
            return
        result = await self.engine.transmit(
            self.peer,
            Action("text", 0, pending.text),
            "chat",
            f"chat:{self.peer}:{pending.trigger}",
            verify=self.verify,
        )
        if result == "WAIT":
            return
        self.pending = None
        if result == "SENT":
            with self.engine.store.connect() as db:
                row = db.execute(
                    "SELECT result_id FROM bw_action WHERE action_key=?",
                    (f"chat:{self.peer}:{pending.trigger}",),
                ).fetchone()
            self.engine.store.set_state(
                self.peer,
                last_outbound=self.engine.clock().isoformat(),
                last_outbound_id=row[0] if row else None,
            )
        self.engine.store.audit(self.peer, "chat_result", result)
